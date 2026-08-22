"""
Verifica preturile pe piata secundara pentru seturile din registru.

RULEAZA O DATA PE SAPTAMANA, nu mai des. O cautare per produs. E de ordine de
marime mai bland decat un scan de magazin, dar tot deschide un browser real.

    python tools/verifica_preturi.py                    # toate seturile S/A
    python tools/verifica_preturi.py --piata vinted
    python tools/verifica_preturi.py --termen "pokemon 30th celebration etb"
    python tools/verifica_preturi.py --vizibil          # vezi ce face browserul

Preturile intra cu incredere MICA: OLX si Vinted afiseaza cereri, nu vanzari.
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

from modules import policy, price_check

PAUZA = 20  # secunde intre cautari — deliberat lent


def termeni_de_verificat():
    """Seturile tier S si A din registru, cu termen de cautare construit."""
    iesire = []
    seturi = policy.incarca_seturi()
    for nisa in policy.nise_configurate():
        for nume_set, d in (seturi.get(nisa) or {}).items():
            if nume_set.startswith("_") or d.get("tier") not in ("S", "A"):
                continue
            linie = nisa.split()[0].lower()
            # Piata se alege dupa data de lansare: un set care apare in
            # septembrie nu are cum sa fie pe OLX in august.
            piata = price_check.piata_recomandata(d.get("lanseaza_la", ""))
            for tip, eticheta in (("booster_box", "booster box"), ("etb", "elite trainer box")):
                if tip in (policy.incarca_politica().get(nisa) or {}).get("urmareste", []):
                    iesire.append((f"{linie}|{nume_set.replace(' ', '-')}|{tip.replace('_','-')}",
                                   f"{linie} {nume_set} {eticheta}", piata))
    return iesire


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--piata", choices=list(price_check.PIETE),
                    help="forteaza o piata; implicit se alege dupa data lansarii")
    ap.add_argument("--termen", help="verifica un singur termen")
    ap.add_argument("--id", help="id canonic pentru --termen")
    ap.add_argument("--vizibil", action="store_true")
    ap.add_argument("--detalii", action="store_true",
                    help="arata ce carduri a potrivit si ce preturi a luat din fiecare")
    a = ap.parse_args()

    headless = not a.vizibil

    if a.termen:
        r = price_check.cauta_pret(a.termen, a.piata or "olx", headless=headless)
        if a.detalii:
            print(f"carduri citite {r.get('carduri_citite')}, respinse {r.get('carduri_respinse')}")
            for d in r.get("detalii", []):
                print(f"  {d['preturi']}  <- {d['titlu']}")
            print()
        print({k: v for k, v in r.items() if k != "detalii"})
        if r.get("ok") and a.id:
            price_check.inregistreaza_pret(a.id, r["median_estimat"], a.piata or "olx", r["esantion"])
        return 0

    lista = termeni_de_verificat()
    print(f"Verific {len(lista)} produse, cu {PAUZA}s intre ele.")
    print("Dureaza ~{:.0f} minute. Nu intrerupe.\n".format(len(lista) * (PAUZA + 15) / 60))

    for i, (id_canonic, termen, piata_auto) in enumerate(lista, 1):
        piata = a.piata or piata_auto
        print(f"[{i}/{len(lista)}] {termen}  ->  {piata}")
        r = price_check.actualizeaza_pret(id_canonic, termen, piata, headless=headless)
        if r.get("ok"):
            print(f"    ~{r['median_estimat']:.0f} lei (cereri {r['median_cereri']:.0f}, "
                  f"{r['esantion']} anunturi, {r['min']:.0f}-{r['max']:.0f})")
        else:
            print(f"    {r.get('motiv')}")
        if i < len(lista):
            time.sleep(PAUZA)

    print()
    print(price_check.raport().replace("<b>","").replace("</b>","").replace("<code>","").replace("</code>",""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
