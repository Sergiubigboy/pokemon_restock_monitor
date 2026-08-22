"""
Simulator local pentru motorul de decizie pe watchlist.

NU pornește Playwright, NU deschide niciun browser, NU trimite nimic pe Telegram
și NU atinge starea reală a botului (contorul de alerte merge într-un fișier
temporar, șters la final).

Alimentează motorul cu produse false și arată exact ce alerte ar fi ieșit.

Rulare:
    python tools/simulate_watchlist.py
    python tools/simulate_watchlist.py --mesaje
    python tools/simulate_watchlist.py --file produse.json
    python tools/simulate_watchlist.py --produs "Pokemon 30th Celebration ETB" \
                                       --site "Pokemon TCG - Krit" --pret "289,00 lei"

Formatul pentru --file: listă JSON de obiecte
    [{"name": "...", "site": "...", "price": "289,00 lei", "url": "...", "qty": 6}]
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Consola Windows e pe cp1252 și crapă la emoji. Forțăm UTF-8 pe ieșire.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from modules import watchlist as wl
from modules.notifier import build_watchlist_message
from modules.price_parser import format_ron, parse_price_ron

LATIME = 100


def _linie(caracter="─"):
    print(caracter * LATIME)


def _pret_romanesc(valoare: float) -> str:
    """1017 -> "1.017,00 lei" — ca să trecem și parserul prin formatul real."""
    intreg = f"{int(round(valoare)):,}".replace(",", ".")
    return f"{intreg},00 lei"


def _site_cunoscut(item: dict, site_uri_reale: set) -> str | None:
    """
    Primul site al item-ului care există și în sites_config.json.
    Item-urile care indică doar spre magazine neconfigurate (LEGO.com RO,
    BrickDepot, Carturesti, Mattel Creations) nu pot fi simulate realist.
    """
    for s in (item.get("buy") or {}).get("sites") or []:
        if s in site_uri_reale:
            return s
    return None


def _nume_din_reguli(item: dict) -> str:
    """Sintetizează un nume de produs care sigur trece regulile item-ului."""
    reguli = item.get("match") or {}
    bucati = list(reguli.get("include_all") or [])
    include_any = reguli.get("include_any") or []
    if include_any:
        bucati.append(include_any[0])
    return " ".join(bucati).strip() or str(item.get("label", "produs"))


def genereaza_cazuri(watchlist: dict, site_uri_reale: set) -> list:
    """
    Construiește cazurile de test DIN watchlist-ul curent, nu dintr-o listă fixă.

    Așa simulatorul rămâne util și după ce agentul săptămânal rotește nișele —
    nu trebuie actualizat manual în fiecare luni.
    """
    cazuri = []

    for item in watchlist.get("items", []):
        if not item.get("enabled"):
            continue

        site = _site_cunoscut(item, site_uri_reale)
        if site is None:
            cazuri.append({
                "name": _nume_din_reguli(item),
                "site": (item.get("buy") or {}).get("sites", ["?"])[0],
                "price": "199,00 lei",
                "nota": "site neconfigurat în sites_config.json",
            })
            continue

        nume = _nume_din_reguli(item)
        plafon = float((item.get("buy") or {}).get("max_price_ron", 0) or 0)

        if plafon > 0:
            cazuri.append({"name": nume, "site": site, "price": _pret_romanesc(plafon * 0.8),
                           "nota": "preț bun (80% din plafon)"})
            cazuri.append({"name": nume, "site": site, "price": _pret_romanesc(plafon * 1.1),
                           "nota": "preț peste plafon"})
        else:
            cazuri.append({"name": nume, "site": site, "price": "0 lei",
                           "nota": "item de tip eveniment"})

        cazuri.append({"name": nume, "site": site, "price": "N/A",
                       "nota": "selector de preț stricat"})

    # Câteva produse care NU trebuie să prindă nimic pe watchlist —
    # ele demonstrează că fluxul clasic rămâne cel care le notifică.
    for nume in ("Pokemon Sleeve Protector 65buc", "Jucarie plus Pikachu 20cm",
                 "Pokemon Sketbook de colorat"):
        cazuri.append({"name": nume, "site": "Pokemon TCG - Krit", "price": "49,99 lei",
                       "nota": "trebuie să NU se potrivească"})

    return cazuri


def ruleaza(cazuri: list, watchlist: dict, arata_mesaje: bool):
    alerte = []
    respinse = 0
    nepotrivite = 0

    _linie("═")
    print(f"  SIMULARE WATCHLIST · {len(cazuri)} produse false")
    eticheta = (watchlist.get("_meta") or {}).get("week_label")
    if eticheta:
        print(f"  Watchlist: {eticheta}")
    if wl.watchlist_is_stale(watchlist):
        print("  ⚠️  ATENȚIE: watchlist EXPIRAT (_meta.valid_until a trecut)")
    _linie("═")
    print()

    for caz in cazuri:
        nume = caz["name"]
        site = caz.get("site", "")
        pret_brut = caz.get("price", "")
        produs = {
            "name": nume,
            "url": caz.get("url", "https://exemplu.ro/produs"),
            "image": caz.get("image"),
            "price": pret_brut,
        }

        pret = parse_price_ron(pret_brut)
        pret_afisat = f"{format_ron(pret)} RON" if pret is not None else "NEDETECTABIL"

        print(f"📦 {nume}")
        print(f"   magazin: {site}   |   preț brut: '{pret_brut}' → {pret_afisat}")
        if caz.get("nota"):
            print(f"   caz: {caz['nota']}")

        item = wl.match_item(nume, site, watchlist)
        if item is None:
            nepotrivite += 1
            print("   ➜ ⚪ NU se potrivește pe watchlist → merge pe fluxul clasic (VIP/blacklist)")
            print()
            continue

        decizie = wl.evaluate(produs, item, watchlist)
        print(f"   potrivit pe: [{decizie.tier}] {decizie.label}  ({decizie.item_id})")

        if decizie.should_alert:
            alerte.append((decizie, produs, site, caz.get("qty")))
            print(f"   ➜ 🚨 ALERTĂ DE CUMPĂRARE"
                  f"   net {decizie.net_profit_ron:+.0f} RON"
                  f" · ROI {decizie.roi_pct * 100:+.0f}%"
                  f" · {decizie.max_qty} buc → {decizie.total_profit_ron:+.0f} RON")
            # Consumăm cota zilnică exact ca în producție, ca să se vadă
            # când limita de alerte pe item chiar intră în joc.
            wl.record_alert(decizie.item_id)
        else:
            respinse += 1
            simbol = "🟡 HEADS_UP" if decizie.kind == "HEADS_UP" else "🔴 RESPINS"
            print(f"   ➜ {simbol}: {decizie.reason}")
        print()

    _linie("═")
    print(f"  REZULTAT: {len(alerte)} alerte de cumpărare · "
          f"{respinse} respinse · {nepotrivite} fără potrivire (flux clasic)")
    _linie("═")

    if arata_mesaje and alerte:
        print("\n\nMESAJELE TELEGRAM CARE AR FI PLECAT:\n")
        for decizie, produs, site, qty in alerte:
            _linie()
            mesaj = build_watchlist_message(decizie, produs["name"], produs["url"], site, qty)
            # Curățăm tag-urile HTML ca să se citească în terminal.
            mesaj = re.sub(r"<a href='([^']*)'>([^<]*)</a>", r"\2 → \1", mesaj)
            for tag in ("<b>", "</b>", "<i>", "</i>", "<pre>", "</pre>"):
                mesaj = mesaj.replace(tag, "")
            print(mesaj)
            print()

    return len(alerte)


def main():
    parser = argparse.ArgumentParser(description="Simulator watchlist — fără scraper, fără Telegram")
    parser.add_argument("--watchlist", default="config/watchlist.json", help="calea către watchlist")
    parser.add_argument("--file", help="fișier JSON cu produse false")
    parser.add_argument("--produs", help="testează un singur produs")
    parser.add_argument("--site", default="Pokemon TCG - Krit", help="magazinul pentru --produs")
    parser.add_argument("--pret", default="289,00 lei", help="prețul brut pentru --produs")
    parser.add_argument("--mesaje", action="store_true", help="afișează mesajele Telegram complete")
    args = parser.parse_args()

    watchlist = wl.load_watchlist(args.watchlist)
    if not watchlist:
        print(f"❌ Nu am putut încărca {args.watchlist}. Vezi mesajul de mai sus.")
        return 1

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            cazuri = json.load(f)
    elif args.produs:
        cazuri = [{"name": args.produs, "site": args.site, "price": args.pret}]
    else:
        try:
            with open("config/sites_config.json", "r", encoding="utf-8") as f:
                site_uri_reale = {s["name"] for s in json.load(f)}
        except Exception:
            site_uri_reale = set()
        cazuri = genereaza_cazuri(watchlist, site_uri_reale)

    # Contorul de alerte merge într-un fișier temporar — starea reală a
    # botului rămâne neatinsă, oricâte simulări rulezi.
    director = tempfile.mkdtemp(prefix="sim_watchlist_")
    original = wl.ALERT_COUNTS_FILE
    wl.ALERT_COUNTS_FILE = os.path.join(director, "alert_counts.json")
    try:
        ruleaza(cazuri, watchlist, args.mesaje)
    finally:
        wl.ALERT_COUNTS_FILE = original
        shutil.rmtree(director, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
