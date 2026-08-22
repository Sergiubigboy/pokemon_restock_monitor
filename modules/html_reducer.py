"""
Reduce o pagina HTML la grila de produse.

O pagina de magazin are 500KB-2MB. Trimisa intreaga la un LLM inseamna bani
aruncati, raspuns lent si selectori mai prosti (modelul se pierde in meniuri,
footer si scripturi de tracking).

Modulul face doua lucruri:
  1. curata: scoate script/style/svg si atributele fara valoare pentru selectori
  2. localizeaza grila: gaseste containerul cu cele mai multe elemente-copil
     care se repeta identic — adica exact lista de produse

Rezultatul tipic e sub 30KB, adica de zeci de ori mai ieftin de procesat.
"""

import re

from bs4 import BeautifulSoup, Comment

# Elemente care nu ajuta niciodata la gasirea selectorilor de produs.
_ETICHETE_DE_STERS = (
    "script", "style", "noscript", "svg", "path", "iframe", "canvas",
    "head", "meta", "link", "template", "video", "audio", "source",
)

# Atribute pastrate: pe astea se construiesc selectorii CSS.
_ATRIBUTE_PASTRATE = ("class", "id", "href", "src", "alt", "title", "itemprop")

# Numarul de produse-exemplu trimise modelului. Trei sunt suficiente ca sa
# deduca tiparul; mai multe doar umfla costul.
_CARDURI_EXEMPLU = 3


def curata_html(html: str) -> BeautifulSoup:
    """Scoate zgomotul si atributele inutile."""
    supa = BeautifulSoup(html, "html.parser")

    for eticheta in _ETICHETE_DE_STERS:
        for element in supa.find_all(eticheta):
            element.decompose()

    for comentariu in supa.find_all(string=lambda t: isinstance(t, Comment)):
        comentariu.extract()

    for element in supa.find_all(True):
        atribute_noi = {}
        for nume, valoare in (element.attrs or {}).items():
            if nume in _ATRIBUTE_PASTRATE or nume.startswith("data-"):
                # Clasele generate de build (hash-uri lungi) nu ajuta la nimic.
                if isinstance(valoare, list):
                    valoare = [v for v in valoare if len(v) < 60]
                atribute_noi[nume] = valoare
        element.attrs = atribute_noi

    return supa


# Un pret romanesc in textul unui card: "289,00 lei", "1.017 RON", "19.99 Lei".
_RE_PRET = re.compile(r"\d[\d.,\s]*\s*(?:lei|ron)\b", re.IGNORECASE)


def _semnatura(element) -> tuple:
    """Doua carduri de produs au aceeasi eticheta si aceleasi clase."""
    return (element.name, tuple(sorted(element.get("class") or [])))


def _scor_card(exemplu, repetitii: int) -> int:
    """
    Cat de mult seamana un element repetat cu un card de produs.

    Numarul de repetitii si cantitatea de text nu ajung: o sectiune de FAQ are
    si ea multe elemente identice cu text lung, si castiga in fata grilei de
    produse. Diferenta o fac semnalele specifice unui produs — pret, imagine,
    link. Bonusurile sunt multiplicative, ca un card adevarat sa domine clar.
    """
    text = exemplu.get_text(" ", strip=True)
    if len(text) < 15:
        return 0  # prea putin text: e navigatie, nu produse

    scor = repetitii * min(len(text), 300)

    if _RE_PRET.search(text):
        scor *= 4                      # cel mai puternic semnal
    if exemplu.find("img") is not None:
        scor *= 2
    if exemplu.find("a", href=True) is not None:
        scor *= 2

    return scor


def gaseste_grila(supa: BeautifulSoup):
    """
    Containerul care seamana cel mai mult cu o grila de produse.

    Se uita la fiecare element cu cel putin 3 copii identici ca structura si
    alege candidatul cu scorul cel mai mare (vezi _scor_card).
    """
    cel_mai_bun = None
    scor_maxim = 0

    for parinte in supa.find_all(True):
        copii = [c for c in parinte.find_all(recursive=False) if c.name]
        if len(copii) < 3:
            continue

        frecvente = {}
        for copil in copii:
            semn = _semnatura(copil)
            frecvente[semn] = frecvente.get(semn, 0) + 1

        semn_dominant, repetitii = max(frecvente.items(), key=lambda kv: kv[1])
        if repetitii < 3:
            continue

        exemplu = next(c for c in copii if _semnatura(c) == semn_dominant)
        scor = _scor_card(exemplu, repetitii)
        if scor > scor_maxim:
            scor_maxim = scor
            cel_mai_bun = (parinte, semn_dominant, repetitii)

    return cel_mai_bun


def extrage_fragment(html: str, max_caractere: int = 30000) -> tuple[str, int]:
    """
    Intoarce (fragment_html, numar_carduri_detectate).

    Daca gaseste grila, trimite doar primele cateva carduri. Daca nu,
    cade inapoi pe HTML-ul curatat si taiat — mai bine ceva decat nimic.
    """
    supa = curata_html(html)
    rezultat = gaseste_grila(supa)

    if rezultat is None:
        text = str(supa)
        return text[:max_caractere], 0

    parinte, semn_dominant, repetitii = rezultat
    carduri = [c for c in parinte.find_all(recursive=False)
               if c.name and _semnatura(c) == semn_dominant]

    bucati = [str(c) for c in carduri[:_CARDURI_EXEMPLU]]
    fragment = (
        f"<!-- container grila: <{parinte.name} "
        f"class=\"{' '.join(parinte.get('class') or [])}\"> "
        f"cu {repetitii} carduri similare -->\n"
        + "\n".join(bucati)
    )

    # Normalizam spatiile: HTML-ul indentat consuma tokeni degeaba.
    fragment = re.sub(r"\n\s*\n", "\n", fragment)
    return fragment[:max_caractere], repetitii
