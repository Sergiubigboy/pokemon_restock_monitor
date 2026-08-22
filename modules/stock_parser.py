"""
Extragerea cantitatii din textul de stoc.

Magazinele scriu stocul in zeci de feluri: "6 buc in stoc", "Stoc: 6",
"ultimele 3 produse", "In stoc" (fara numar). Cand nu exista un numar,
returnam None si alerta se trimite fara linia de cantitate — mai bine lipsa
decat o cifra inventata, pentru ca pe baza ei decizi cate bucati cumperi.
"""

import re
import unicodedata

# Peste pragul asta e clar altceva (cod de produs, pret, an), nu cantitate.
_MAX_REZONABIL = 9999

# Tiparele sunt incercate in ordine; primul care prinde castiga.
_TIPARE = (
    # "6 buc", "6 bucati", "12 produse", "3 articole"
    re.compile(r"(\d{1,4})\s*(?:buc\b|bucati|bucata|produse|articole|item[es]?)\b"),
    # "stoc: 6", "in stoc 6", "stoc disponibil: 12"
    re.compile(r"stoc(?:\s+disponibil)?\s*[:\-]?\s*(\d{1,4})\b"),
    # "ultimele 3", "ultimul 1"
    re.compile(r"ultim(?:ele|ul|a)\s+(\d{1,4})\b"),
    # "disponibil: 6", "disponibile 12"
    re.compile(r"disponibil[ei]?\s*[:\-]?\s*(\d{1,4})\b"),
    # "cantitate: 6"
    re.compile(r"cantitate\s*[:\-]?\s*(\d{1,4})\b"),
)


def _normalizeaza(text: str) -> str:
    """lowercase fara diacritice — "bucăți" si "bucati" trebuie tratate la fel."""
    descompus = unicodedata.normalize("NFKD", str(text))
    fara_diacritice = "".join(c for c in descompus if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", fara_diacritice).strip().lower()


def parse_stock_qty(text) -> int | None:
    """
    Scoate cantitatea din textul de stoc. None daca nu exista un numar explicit.

    >>> parse_stock_qty("6 buc in stoc")
    6
    >>> parse_stock_qty("In stoc")      # fara numar
    >>> parse_stock_qty("Ultimele 3 produse")
    3
    """
    if text is None:
        return None

    if isinstance(text, int) and not isinstance(text, bool):
        return text if 0 < text <= _MAX_REZONABIL else None

    if not isinstance(text, str):
        return None

    normalizat = _normalizeaza(text)
    if not normalizat:
        return None

    for tipar in _TIPARE:
        potrivire = tipar.search(normalizat)
        if potrivire:
            try:
                valoare = int(potrivire.group(1))
            except ValueError:
                continue
            if 0 < valoare <= _MAX_REZONABIL:
                return valoare

    return None
