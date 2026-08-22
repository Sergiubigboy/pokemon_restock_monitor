"""
Verifica ce magazine pot trece pe fast-path-ul HTTP, fara browser.

Face O SINGURA cerere GET per site (mai putin decat o scanare normala) si
raporteaza cate produse ies pe calea HTTP fata de configuratia curenta.

    python tools/test_http_engine.py
    python tools/test_http_engine.py --site krit

Daca un site raporteaza produse pe HTTP, poti adauga in sites_config.json:
    "engine": "http"
si magazinul nu mai porneste Edge deloc. Daca da 0 produse sau HTTP 403,
LASA-L pe Playwright — inseamna ca randeaza din JS sau are anti-bot.

Scriptul NU modifica sites_config.json. Comutarea o faci tu, dupa ce vezi
cifrele.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import logging

from modules.http_scraper import check_search_page_stock_http

# Pauza intre magazine — nu vrem sa arate ca un scan agresiv.
PAUZA_INTRE_SITEURI = 3


def main():
    parser = argparse.ArgumentParser(description="Testeaza fast-path-ul HTTP per magazin")
    parser.add_argument("--site", help="testeaza doar magazinele care contin textul asta in nume")
    parser.add_argument("--verbose", action="store_true", help="arata primele produse gasite")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    with open("config/sites_config.json", "r", encoding="utf-8") as f:
        sites = json.load(f)

    if args.site:
        sites = [s for s in sites if args.site.lower() in s["name"].lower()]
        if not sites:
            print(f"Niciun magazin nu contine '{args.site}'.")
            return 1

    print("=" * 78)
    print("  TEST FAST-PATH HTTP — o singura cerere per magazin")
    print("=" * 78)
    print()

    pot_trece = []

    for index, site in enumerate(sites):
        nume = site["name"]
        # Fortam calea HTTP indiferent de ce scrie in config.
        config_test = dict(site)
        config_test["engine"] = "http"

        start = time.time()
        produse = check_search_page_stock_http(config_test)
        durata = time.time() - start

        motor_curent = site.get("engine", "playwright")
        headless = site.get("headless", True)
        cost = "HTTP" if motor_curent == "http" else ("Edge vizibil" if not headless else "Edge headless")

        if produse:
            pot_trece.append(nume)
            print(f"✅ {nume}")
            print(f"   {len(produse)} produse în {durata:.1f}s   |   acum rulează pe: {cost}")
            if args.verbose:
                for p in produse[:3]:
                    from modules.price_parser import parse_price_ron
                    pret = parse_price_ron(p["price"])
                    qty = f" · {p['qty']} buc" if p.get("qty") else ""
                    print(f"      - {p['name'][:55]}")
                    print(f"        preț: {pret if pret is not None else 'NEDETECTABIL'}{qty}")
                    print(f"        link: {p['url'][:80]}")
        else:
            print(f"❌ {nume}")
            print(f"   0 produse pe HTTP — trebuie să rămână pe Playwright   |   acum: {cost}")
        print()

        if index < len(sites) - 1:
            time.sleep(PAUZA_INTRE_SITEURI)

    print("=" * 78)
    if pot_trece:
        print(f"  {len(pot_trece)} din {len(sites)} magazine pot trece pe HTTP:")
        for nume in pot_trece:
            print(f"    • {nume}")
        print()
        print('  Adaugă "engine": "http" la fiecare, în config/sites_config.json.')
    else:
        print("  Niciun magazin nu merge pe HTTP. Toate rămân pe Playwright.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
