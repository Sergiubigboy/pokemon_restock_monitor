"""
Gaseste categoriile celorlalte nise pe magazinele pe care le ai deja.

Ideea, evidenta dupa ce o spui: un magazin care vinde Pokemon vinde aproape
sigur si One Piece, Magic, Lorcana. Selectorii sunt ACEIASI — e acelasi site,
aceeasi tema. Singurul lucru care difera e URL-ul categoriei.

Deci nu e nevoie de generare noua de config, nici de vreun apel LLM: iei
configul existent al magazinului, schimbi doar URL-ul, si validezi.

    python tools/descopera_categorii.py                  # doar raporteaza
    python tools/descopera_categorii.py --save           # adauga ce trece validarea
    python tools/descopera_categorii.py --nisa "One Piece TCG"

Validarea e aceeasi ca la generatorul de configuri: minim 3 produse cu pret
parsabil. O categorie goala NU se adauga — ar declansa alarma de "site cazut".
"""

import argparse
import json
import os
import sys
import time
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import logging
logging.disable(logging.CRITICAL)

import requests
from bs4 import BeautifulSoup

from modules.http_scraper import check_search_page_stock_http, ANTET_IMPLICIT
from modules.price_parser import parse_price_ron

# Cuvintele din URL care tradeaza categoria fiecarei nise.
CUVINTE_NISA = {
    "One Piece TCG":        ["one-piece", "onepiece", "one_piece"],
    "Magic The Gathering":  ["magic-the-gathering", "magic", "mtg"],
    "Riftbound":            ["riftbound"],
    "Disney Lorcana":       ["lorcana"],
    "Yu-Gi-Oh":             ["yu-gi-oh", "yugioh"],
}

# Linkuri care contin cuvantul dar nu sunt categorii.
EXCLUSE = ("/produs", "/product", "/p/", "cart", "cos", "wishlist", "compare",
           "single", "/blog", "/news", "javascript:", "#")

PAUZA = 3


def domenii_unice(sites: list) -> dict:
    """Un singur config-sablon per domeniu — selectorii sunt ai temei, nu ai categoriei."""
    pe_domeniu = {}
    for s in sites:
        if s.get("engine") != "http":
            continue          # sabloanele cu browser nu le atingem aici
        d = urlparse(s["url"]).netloc
        pe_domeniu.setdefault(d, s)
    return pe_domeniu


def cauta_linkuri(baza_url: str) -> list:
    parsat = urlparse(baza_url)
    radacina = f"{parsat.scheme}://{parsat.netloc}/"
    try:
        r = requests.get(radacina, headers=ANTET_IMPLICIT, timeout=25)
        if r.status_code != 200:
            return []
        supa = BeautifulSoup(r.text, "html.parser")
        return [urljoin(radacina, a.get("href", "")) for a in supa.select("a[href]")]
    except Exception:
        return []


# Cuvinte care trebuie sa apara in NUMELE produselor, ca sa fim siguri ca
# am nimerit categoria si nu o pagina vecina.
CUVINTE_IN_NUME = {
    "One Piece TCG":       ["one piece"],
    "Magic The Gathering": ["magic", "mtg"],
    "Riftbound":           ["riftbound"],
    "Disney Lorcana":      ["lorcana"],
    "Yu-Gi-Oh":            ["yu-gi-oh", "yugioh", "yu gi oh"],
}


def valideaza(sablon: dict, url: str, nume: str, nisa: str):
    """
    Configul candidat + rezultatul unui scrape de proba.

    Nu e destul sa iasa produse: trebuie sa fie produsele NISEI. Fara
    verificarea asta, o categorie de binder-uri de pe pokemania.ro a trecut
    drept "Magic" doar pentru ca URL-ul continea cuvantul.
    """
    candidat = dict(sablon)
    candidat.update({"name": nume, "url": url, "niche": nisa})
    produse = check_search_page_stock_http(candidat)
    preturi = [x for x in (parse_price_ron(p["price"]) for p in produse) if x]

    cuvinte = CUVINTE_IN_NUME.get(nisa, [])
    potrivite = [p for p in produse
                 if any(c in p["name"].lower() for c in cuvinte)] if cuvinte else produse

    ok = (len(produse) >= 3
          and len(preturi) >= len(produse) * 0.7
          and len(potrivite) >= len(produse) * 0.6)
    return candidat, produse, preturi, ok, len(potrivite)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="adauga ce trece validarea")
    ap.add_argument("--nisa", help="cauta doar nisa asta")
    args = ap.parse_args()

    cale = "config/sites_config.json"
    sites = json.load(open(cale, encoding="utf-8"))
    existente = {s["url"] for s in sites}
    nume_existente = {s["name"] for s in sites}

    nise = {args.nisa: CUVINTE_NISA[args.nisa]} if args.nisa else CUVINTE_NISA
    sabloane = domenii_unice(sites)

    print(f"Caut {len(nise)} nise pe {len(sabloane)} domenii cunoscute.\n")
    gasite = []

    for domeniu, sablon in sabloane.items():
        linkuri = cauta_linkuri(sablon["url"])
        if not linkuri:
            print(f"── {domeniu}: nu am putut citi pagina principala")
            continue

        print(f"── {domeniu}")
        for nisa, cuvinte in nise.items():
            candidate = sorted({
                l for l in linkuri
                if any(c in l.lower() for c in cuvinte)
                and not any(x in l.lower() for x in EXCLUSE)
            }, key=len)[:2]

            for url in candidate:
                if url in existente:
                    continue
                eticheta = domeniu.replace("www.", "").replace("comenzi.", "").split(".")[0].title()
                nume = f"{nisa} - {eticheta}"
                if nume in nume_existente:
                    continue   # o categorie per nisa per magazin ajunge

                candidat, produse, preturi, ok, potrivite = valideaza(sablon, url, nume, nisa)
                marcaj = "✅" if ok else "❌"
                print(f"   {marcaj} {nisa:22s} {len(produse):>2} produse "
                      f"({potrivite} din nisa)  {url[:56]}")
                if ok:
                    if produse[:1]:
                        print(f"      ex: {produse[0]['name'][:52]} | {preturi[0] if preturi else '?'}")
                    gasite.append(candidat)
                    existente.add(url)
                    nume_existente.add(nume)
                time.sleep(PAUZA)
        print()

    print("=" * 70)
    if not gasite:
        print("Nicio categorie noua valida. Magazinele nu au sau nu au stoc acum.")
        return 0

    print(f"{len(gasite)} categorii noi valide:")
    for c in gasite:
        print(f"   {c['niche']:22s} {c['name']}")

    if args.save:
        sites.extend(gasite)
        with open(cale, "w", encoding="utf-8") as f:
            json.dump(sites, f, ensure_ascii=False, indent=4)
            f.write("\n")
        print(f"\n💾 Salvate. Total: {len(sites)} magazine.")
        print("   Botul le preia la ciclul urmator, fara restart.")
    else:
        print("\n💡 Ruleaza cu --save ca sa le adaug.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
