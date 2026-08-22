"""
Parser de preturi in format romanesc.

Textul vine brut din pagina, prin scraper.py, in formate foarte variate:
    "1.017,00 lei", "599,99 RON", "Pret: 289 lei", "de la 199 lei", "N/A"

Regula de baza in RO este INVERSA fata de conventia engleza:
    punctul  = separator de MII      (1.017 inseamna o mie saptesprezece)
    virgula  = separator ZECIMAL     (29,99 inseamna douazeci si noua virgula 99)

Modulul nu depinde de nimic din afara bibliotecii standard.
"""

import re

# Spatii "exotice" care apar des in HTML si trebuie tratate ca spatiu normal:
# non-breaking space, narrow no-break space, thin space, tab.
_SPATII = ("\xa0", " ", " ", "\t")

# Primul "bloc numeric" din text: cifre legate prin . , sau spatiu.
# Se opreste automat la prima litera ("289 lei" -> "289").
_RE_NUMAR = re.compile(r"\d+(?:[.,\s]\d+)*")


def _bloc_la_float(brut: str) -> float | None:
    """Converteste un bloc numeric deja extras (ex: "1.017,00") in float."""
    # Spatiile sunt intotdeauna separator de mii, niciodata zecimal.
    text = re.sub(r"\s+", "", brut).strip(".,")
    if not text:
        return None

    separatoare = [c for c in text if c in ".,"]

    # Fara separatoare: numar intreg simplu ("289").
    if not separatoare:
        try:
            return float(text)
        except ValueError:
            return None

    poz_ultim = max(text.rfind("."), text.rfind(","))
    parte_zecimala = text[poz_ultim + 1:]

    if len(separatoare) == 1:
        # Un singur separator. Regula practica ceruta: daca dupa el urmeaza
        # exact 3 cifre, e separator de MII, nu zecimal.
        #   "1.017" -> 1017.0        (o mie saptesprezece, nu 1.017)
        #   "2.50"  -> 2.5           (doua cifre => zecimale)
        # Aplicam regula si virgulei: niciun pret real din RO nu are 3 zecimale,
        # deci "1,017" e tot o mie saptesprezece. Directia asta e cea sigura —
        # varianta gresita ar citi 1017 lei ca 1 leu si ar declansa alerte false.
        e_separator_mii = len(parte_zecimala) == 3
    else:
        # Mai multe separatoare. Daca sunt toate identice si fiecare grup are
        # exact 3 cifre, sunt toate separatoare de mii ("1.017.500").
        # Altfel ultimul separator e cel zecimal ("1.017,00").
        grupuri = re.split(r"[.,]", text)[1:]
        e_separator_mii = (
            len(set(separatoare)) == 1
            and all(len(g) == 3 for g in grupuri)
        )

    if e_separator_mii:
        numar = text.replace(".", "").replace(",", "")
    else:
        intreg = text[:poz_ultim].replace(".", "").replace(",", "")
        numar = f"{intreg or '0'}.{parte_zecimala}"

    try:
        return float(numar)
    except ValueError:
        return None


def parse_price_ron(text) -> float | None:
    """
    Transforma textul brut de pret intr-un numar.

    Returneaza None daca textul nu contine niciun numar ("N/A", "", "Stoc epuizat").
    None inseamna "pret nedetectabil" — apelantul NU trebuie sa trimita alerta de
    cumparare, pentru ca cel mai probabil s-a stricat selectorul de pret.

    Daca textul contine mai multe preturi (ex: pret taiat + pret redus,
    "1.299,00 lei 999,00 lei"), se ia PRIMUL. E varianta conservatoare: pretul
    vechi e mai mare, deci profitul estimat iese mai mic si nu apar alerte false.
    """
    if text is None:
        return None

    # Acceptam si numere gata parsate, ca sa fie usor de folosit in teste.
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)

    if not isinstance(text, str):
        return None

    # Normalizam spatiile exotice din HTML in spatiu obisnuit.
    normalizat = text
    for spatiu in _SPATII:
        normalizat = normalizat.replace(spatiu, " ")

    # Unele teme sparg pretul in noduri separate si iese "19 . 99 Lei" (vezi
    # Krit). Lipim separatorul de cifre. Spatiile care NU sunt langa un . sau ,
    # raman neatinse, ca "1 017,00" sa fie in continuare o mie saptesprezece.
    normalizat = re.sub(r"\s*([.,])\s*", r"\1", normalizat)

    potrivire = _RE_NUMAR.search(normalizat)
    if not potrivire:
        return None

    return _bloc_la_float(potrivire.group(0))


def format_ron(valoare: float | None) -> str:
    """Formateaza un numar pentru afisare in mesaje ("1017.0" -> "1017")."""
    if valoare is None:
        return "?"
    if abs(valoare - round(valoare)) < 0.005:
        return str(int(round(valoare)))
    return f"{valoare:.2f}".replace(".", ",")
