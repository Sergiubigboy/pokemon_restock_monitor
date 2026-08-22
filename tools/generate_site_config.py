"""
Genereaza automat configuratia unui magazin nou, din HTML, cu Gemini.

Inlocuieste procedura manuala: intri pe pagina categoriei, salvezi HTML-ul,
ceri unui LLM selectorii, ii lipesti in sites_config.json.

    python tools/generate_site_config.py --url "https://magazin.ro/pokemon" \\
                                         --nume "Pokemon TCG - Magazin" \\
                                         --nisa "Pokemon TCG"

Pasii, in ordine:
  1. descarca pagina (HTTP simplu; --browser foloseste Playwright pentru
     site-urile cu anti-bot)
  2. reduce HTML-ul la grila de produse (vezi modules/html_reducer.py)
  3. cere selectorii de la Gemini, cu schema JSON impusa
  4. VALIDEAZA rezultatul rulland un scrape de proba
  5. abia daca validarea trece, propune configuratia (si o salveaza cu --save)

Pasul 4 e cel mai important. Un LLM poate inventa un selector care pare
plauzibil dar nu prinde nimic; fara validare ai ajunge cu un magazin care
raporteaza 0 produse la fiecare ciclu si declanseaza alarma de "site cazut".

Cheia:
    pune GEMINI_API_KEY in config/.env — NU o da pe chat si NU o pune in cod.
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

import requests
from dotenv import load_dotenv

from modules.html_reducer import extrage_fragment
from modules.http_scraper import check_search_page_stock_http
from modules.price_parser import parse_price_ron

load_dotenv(dotenv_path="config/.env")

MODEL_IMPLICIT = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
SITES_CONFIG = "config/sites_config.json"

# Schema impusa raspunsului. Cu ea, Gemini nu mai poate returna proza sau
# JSON stricat — primim exact cheile de care are nevoie sites_config.json.
SCHEMA_RASPUNS = {
    "type": "object",
    "properties": {
        "card_selector": {"type": "string"},
        "title_selector": {"type": "string"},
        "price_selector": {"type": "string"},
        "image_selector": {"type": "string"},
        "link_selector": {"type": "string"},
        "qty_selector": {"type": "string"},
        "in_stock_text": {"type": "string"},
        "explicatie": {"type": "string"},
    },
    "required": ["card_selector", "title_selector", "price_selector", "explicatie"],
}

PROMPT = """Esti un expert in scraping de magazine online romanesti.

Primesti un fragment de HTML dintr-o pagina de categorie/cautare a magazinului
{url}. Fragmentul contine cateva carduri de produs consecutive.

Returneaza selectorii CSS pentru extragerea produselor:

- card_selector: selectorul UNUI card de produs (relativ la document). Trebuie
  sa prinda toate cardurile din grila.
- title_selector: numele produsului, RELATIV la card
- price_selector: pretul, RELATIV la card
- image_selector: imaginea (tag img), RELATIV la card
- link_selector: linkul catre pagina produsului (tag a), RELATIV la card.
  Atentie: multe carduri au mai intai un <a> de wishlist sau "adauga in cos".
  Alege-l pe cel care duce la pagina produsului.
- qty_selector: elementul care contine cantitatea in stoc, RELATIV la card.
  Sir gol daca magazinul nu afiseaza cantitati.
- in_stock_text: un text scurt, cu litere mici, care apare DOAR in cardurile
  produselor disponibile (ex: "adauga in cos"). Sir gol daca pagina afiseaza
  numai produse disponibile.
- explicatie: o propozitie in romana despre cum ai ales selectorii.

Reguli stricte:
- foloseste clase stabile, nu hash-uri generate la build
- prefera selectori scurti si robusti
- nu inventa clase care nu apar in HTML-ul primit
- selectorii relativi NU trebuie sa repete card_selector

HTML:
{html}
"""


def descarca_html(url: str, foloseste_browser: bool, profil: str) -> str:
    """Ia HTML-ul paginii. Playwright doar daca site-ul chiar are nevoie."""
    if not foloseste_browser:
        antet = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"
            ),
            "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
        }
        raspuns = requests.get(url, headers=antet, timeout=30)
        raspuns.raise_for_status()
        return raspuns.text

    # Calea cu browser refoloseste exact aceeasi configuratie anti-bot ca botul.
    from playwright.sync_api import sync_playwright

    director_profil = os.path.join(os.getcwd(), "config", "profiles", profil)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=director_profil,
            channel="msedge",
            headless=False,
            viewport={"width": 1280, "height": 720},
            args=["--disable-blink-features=AutomationControlled",
                  "--disable-infobars", "--window-position=-3000,0"],
        )
        try:
            pagina = context.pages[0] if context.pages else context.new_page()
            pagina.goto(url, wait_until="domcontentloaded", timeout=45000)
            pagina.wait_for_timeout(4000)
            return pagina.content()
        finally:
            context.close()


def intreaba_gemini(fragment_html: str, url: str, model: str, cheie: str) -> dict:
    """Un singur apel REST — fara SDK, deci fara grpc/protobuf pe Pi."""
    endpoint = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent")

    corp = {
        "contents": [{
            "parts": [{"text": PROMPT.format(url=url, html=fragment_html)}]
        }],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA_RASPUNS,
        },
    }

    raspuns = requests.post(
        endpoint,
        headers={"x-goog-api-key": cheie, "Content-Type": "application/json"},
        json=corp,
        timeout=90,
    )

    if raspuns.status_code != 200:
        raise RuntimeError(f"Gemini a raspuns cu {raspuns.status_code}: {raspuns.text[:400]}")

    date = raspuns.json()
    try:
        text = date["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Raspuns Gemini neasteptat: {json.dumps(date)[:400]}")

    return json.loads(text)


def valideaza(config_candidat: dict) -> tuple[bool, list, list]:
    """
    Ruleaza un scrape de proba si verifica daca rezultatul e utilizabil.

    Intoarce (e_valid, produse, probleme).
    """
    probleme = []
    produse = check_search_page_stock_http(config_candidat)

    if not produse:
        return False, [], ["0 produse extrase — selectorul de card e gresit "
                           "sau pagina se randeaza din JavaScript"]

    nume = [p["name"] for p in produse if p["name"] and p["name"] != "Necunoscut"]
    if len(nume) < len(produse) * 0.7:
        probleme.append(f"doar {len(nume)}/{len(produse)} produse au nume valid")

    if len(set(nume)) < max(2, len(nume) * 0.5):
        probleme.append("numele produselor se repeta — title_selector prinde "
                        "un element comun, nu titlul cardului")

    preturi = [parse_price_ron(p["price"]) for p in produse]
    cu_pret = [x for x in preturi if x is not None and x > 0]
    if len(cu_pret) < len(produse) * 0.7:
        probleme.append(f"doar {len(cu_pret)}/{len(produse)} produse au pret parsabil")

    linkuri = [p["url"] for p in produse if p["url"]]
    linkuri_utile = [u for u in linkuri if u.rstrip("/") != config_candidat["url"].rstrip("/")]
    if len(set(linkuri_utile)) < max(2, len(produse) * 0.5):
        probleme.append("linkurile produselor sunt identice sau duc la pagina "
                        "de categorie — link_selector e gresit")

    return (len(probleme) == 0), produse, probleme


def main():
    parser = argparse.ArgumentParser(description="Genereaza config de magazin cu Gemini")
    parser.add_argument("--url", help="pagina de categorie/cautare a magazinului")
    parser.add_argument("--nume", help="numele magazinului, exact cum va aparea in watchlist")
    parser.add_argument("--nisa", default="Pokemon TCG", help="nisa magazinului")
    parser.add_argument("--browser", action="store_true",
                        help="descarca prin Playwright (pentru site-uri cu anti-bot)")
    parser.add_argument("--profil", default="generator_profile", help="profilul de browser")
    parser.add_argument("--html-file", help="foloseste un HTML salvat, fara sa descarce nimic")
    parser.add_argument("--model", default=MODEL_IMPLICIT)
    parser.add_argument("--save", action="store_true",
                        help="adauga configuratia in sites_config.json daca validarea trece")
    parser.add_argument("--doar-reducere", action="store_true",
                        help="doar arata fragmentul de HTML, fara sa cheme Gemini")
    args = parser.parse_args()

    if not args.html_file and not args.url:
        parser.error("ai nevoie de --url sau de --html-file")

    # ── 1. HTML ────────────────────────────────────────────────
    if args.html_file:
        with open(args.html_file, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
        url = args.url or "https://exemplu.ro"
        print(f"📄 Am citit {len(html):,} caractere din {args.html_file}")
    else:
        url = args.url
        print(f"🌐 Descarc {url} ({'browser' if args.browser else 'HTTP simplu'})...")
        html = descarca_html(url, args.browser, args.profil)
        print(f"📄 Am primit {len(html):,} caractere")

    # ── 2. Reducere ────────────────────────────────────────────
    fragment, carduri = extrage_fragment(html)
    economie = 100 - (len(fragment) * 100 // max(len(html), 1))
    print(f"✂️  Redus la {len(fragment):,} caractere ({economie}% mai putin)"
          f" · {carduri} carduri detectate in grila")

    if carduri == 0:
        print("⚠️  Nu am gasit o grila clara de produse. Fie pagina se randeaza")
        print("    din JavaScript (incearca --browser), fie nu e o pagina de listare.")

    if args.doar_reducere:
        print("\n" + "─" * 70)
        print(fragment[:4000])
        return 0

    # ── 3. Gemini ──────────────────────────────────────────────
    cheie = os.getenv("GEMINI_API_KEY", "").strip()
    if not cheie:
        print("\n❌ GEMINI_API_KEY lipseste.")
        print("   Adauga in config/.env linia:  GEMINI_API_KEY=cheia_ta")
        print("   (config/.env e deja in .gitignore)")
        return 1

    print(f"🤖 Intreb {args.model}...")
    start = time.time()
    try:
        selectori = intreaba_gemini(fragment, url, args.model, cheie)
    except Exception as e:
        print(f"❌ Apelul catre Gemini a esuat: {e}")
        return 1
    print(f"   raspuns in {time.time() - start:.1f}s")
    print(f"   {selectori.get('explicatie', '')}")

    # Daca modelul nu a gasit carduri de produs, spune-o clar aici — altfel
    # validarea ar raporta "0 produse" si ai cauta vina in selectori.
    if not (selectori.get("card_selector") or "").strip():
        print("\n❌ Gemini nu a identificat carduri de produs in fragment.")
        print("   Aproape sigur fragmentul trimis nu e grila de produse.")
        print("   Incearca: --browser (pagina se randeaza din JavaScript),")
        print("   sau --doar-reducere ca sa vezi ce fragment a fost extras.")
        return 1

    # ── 4. Validare ────────────────────────────────────────────
    config_candidat = {
        "name": args.nume or "Magazin nou",
        "url": url,
        "type": "search_page",
        "niche": args.nisa,
        "engine": "http",
        "card_selector": selectori.get("card_selector", ""),
        "title_selector": selectori.get("title_selector", ""),
        "price_selector": selectori.get("price_selector", ""),
        "image_selector": selectori.get("image_selector") or "img",
        "in_stock_text": selectori.get("in_stock_text", ""),
        "profile_folder": (args.nume or "magazin").lower().replace(" ", "_") + "_profile",
    }
    if selectori.get("link_selector"):
        config_candidat["link_selector"] = selectori["link_selector"]
    if selectori.get("qty_selector"):
        config_candidat["qty_selector"] = selectori["qty_selector"]

    if args.html_file:
        print("\n⚠️  Cu --html-file nu pot valida (validarea cere o cerere reala).")
        print(json.dumps(config_candidat, ensure_ascii=False, indent=4))
        return 0

    print("🔬 Validez cu un scrape de proba...")
    e_valid, produse, probleme = valideaza(config_candidat)

    if produse:
        print(f"   {len(produse)} produse extrase. Primele 3:")
        for p in produse[:3]:
            pret = parse_price_ron(p["price"])
            print(f"     • {p['name'][:55]}")
            print(f"       {pret if pret is not None else 'PRET NEDETECTABIL'} | {p['url'][:65]}")

    if not e_valid:
        print("\n❌ Validare ESUATA:")
        for problema in probleme:
            print(f"   • {problema}")
        print("\n   Configuratia NU a fost salvata. Incearca --browser, sau")
        print("   corecteaza manual selectorii de mai jos:")
        print(json.dumps(config_candidat, ensure_ascii=False, indent=4))
        return 1

    print("\n✅ Validare TRECUTA. Configuratie propusa:\n")
    print(json.dumps(config_candidat, ensure_ascii=False, indent=4))

    # ── 5. Salvare ─────────────────────────────────────────────
    if args.save:
        with open(SITES_CONFIG, "r", encoding="utf-8") as f:
            sites = json.load(f)

        if any(s["name"] == config_candidat["name"] for s in sites):
            print(f"\n⚠️  Exista deja un magazin cu numele '{config_candidat['name']}'.")
            print("    Nu am salvat nimic — alege alt --nume.")
            return 1

        sites.append(config_candidat)
        with open(SITES_CONFIG, "w", encoding="utf-8") as f:
            json.dump(sites, f, ensure_ascii=False, indent=4)
            f.write("\n")
        print(f"\n💾 Salvat in {SITES_CONFIG}. Botul il preia la ciclul urmator (hot-reload).")
    else:
        print("\n💡 Ruleaza din nou cu --save ca sa il adaug in sites_config.json.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
