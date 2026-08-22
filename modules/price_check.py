"""
Verificarea preturilor pe piata secundara, cu browser real.

Testat pe 19 august 2026: OLX, Cardmarket si eBay intorc 403 la cereri simple,
iar Vinted intoarce 7,3 MB de JavaScript fara niciun pret in HTML. Toate patru
cer un browser adevarat. Folosim exact aceeasi configuratie anti-bot ca
scraper.py — profil persistent, msedge, AutomationControlled dezactivat.

REGULA CARE SCHIMBA TOTUL
─────────────────────────
OLX si Vinted afiseaza CERERI, nu vanzari. Un anunt la 450 lei nu inseamna ca
produsul face 450 lei — inseamna ca cineva spera. Mediana cererilor e
sistematic peste pretul real cu 15-30%.

De aceea:
  - toate preturile de aici intra cu incredere "mica"
  - se aplica un discount de realism inainte de a fi folosite in calcule
  - semnalul curat e DISPARITIA unui anunt (ala s-a vandut), nu pretul afisat
  - nicio decizie de cumparare nu se ia doar pe datele astea

FRECVENTA
─────────
O cautare pe produs, o data pe saptamana. Asta e de ordine de marime mai bland
decat un scan de magazin si nu seamana cu trafic de bot. NU chema functiile
astea in bucla de scanare.
"""

import json
import logging
import os
import re
import statistics
import threading
import time
from datetime import date
from urllib.parse import quote

PRICE_BOOK_FILE = os.path.join("config", "price_book.json")

# Cat scadem din mediana cererilor ca sa ne apropiem de pretul real de vanzare.
# Valoare conservatoare: mai bine subestimezi profitul decat sa cumperi prost.
DISCOUNT_REALISM = {
    "olx": 0.80,        # cererile de pe OLX sunt cele mai umflate
    "vinted": 0.85,
    "cardmarket": 1.0,  # preturi de vanzare efective, nu cereri
    "tcgplayer": 1.0,
}

# Cate anunturi luam in calcul. Prea putine = zgomot, prea multe = intram in
# categorii vecine.
MIN_ESANTION = 4
MAX_ESANTION = 25

_lock = threading.RLock()
_carte = None


# ─────────────────────────────────────────────────────────────────
#  Registrul de preturi
# ─────────────────────────────────────────────────────────────────
def _incarca() -> dict:
    global _carte
    if _carte is None:
        try:
            with open(PRICE_BOOK_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            _carte = d if isinstance(d, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            _carte = {}
        except Exception as e:
            logging.warning(f"⚠️ [Preturi] Nu am putut citi registrul: {e}")
            _carte = {}
    return _carte


def reseteaza():
    global _carte
    with _lock:
        _carte = None


def _salveaza():
    try:
        tmp = PRICE_BOOK_FILE + ".tmp"
        director = os.path.dirname(PRICE_BOOK_FILE)
        if director:
            os.makedirs(director, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_incarca(), f, ensure_ascii=False, indent=2)
        for i in range(5):
            try:
                os.replace(tmp, PRICE_BOOK_FILE)
                return
            except PermissionError:
                if i == 4:
                    raise
                time.sleep(0.1 * (i + 1))
    except Exception as e:
        logging.warning(f"⚠️ [Preturi] Nu am putut salva registrul: {e}")


def pret_cunoscut(id_canonic: str):
    """Ce stim despre pretul produsului asta. None daca nu stim nimic."""
    with _lock:
        return _incarca().get(id_canonic)


def vechime_zile(id_canonic: str, azi: date = None):
    intrare = pret_cunoscut(id_canonic)
    if not intrare or not intrare.get("verificat_la"):
        return None
    try:
        verificat = date.fromisoformat(str(intrare["verificat_la"])[:10])
        return ((azi or date.today()) - verificat).days
    except ValueError:
        return None


def inregistreaza_pret(id_canonic: str, median_ron: float, sursa: str,
                       esantion: int, incredere: str = "mica", azi: date = None):
    """
    Salveaza un punct de pret, pastrand istoricul.

    Istoricul e ce raspunde la "hold sau sell": doua puncte in timp inseamna
    tendinta, fara sa ai nevoie de nimic "live".
    """
    if not id_canonic or not median_ron or median_ron <= 0:
        return None
    azi_str = (azi or date.today()).isoformat()

    with _lock:
        carte = _incarca()
        intrare = carte.get(id_canonic) or {"istoric": []}
        anterior = intrare.get("median_ron")

        intrare.update({
            "median_ron": round(float(median_ron), 2),
            "sursa": sursa,
            "esantion": int(esantion),
            "incredere": incredere,
            "verificat_la": azi_str,
        })
        istoric = [x for x in intrare.get("istoric", []) if x.get("data") != azi_str]
        istoric.append({"data": azi_str, "median_ron": round(float(median_ron), 2),
                        "sursa": sursa})
        intrare["istoric"] = istoric[-24:]   # ~6 luni de puncte saptamanale

        if anterior:
            intrare["tendinta_pct"] = round((median_ron - anterior) / anterior * 100, 1)

        carte[id_canonic] = intrare
        _salveaza()
    return intrare


# ─────────────────────────────────────────────────────────────────
#  Extragerea preturilor din pagina
# ─────────────────────────────────────────────────────────────────
_RE_PRET_RO = re.compile(r"(\d[\d.\s]{0,9}(?:,\d{1,2})?)\s*(?:lei|ron)", re.IGNORECASE)
# Cardmarket scrie "12,50 €", TCGplayer "$12.50"
_RE_PRET_EUR = re.compile(r"(\d[\d.,\s]{0,9})\s*€|€\s*(\d[\d.,\s]{0,9})")
_RE_PRET_USD = re.compile(r"\$\s*(\d[\d.,\s]{0,9})")


def extrage_preturi(text: str, moneda: str = "RON") -> list:
    """
    Toate sumele dintr-un bloc de text, convertite in LEI.

    Cardmarket afiseaza euro, TCGplayer dolari. Conversia e orientativa —
    cifra finala e oricum marcata cu incredere mica.
    """
    from modules.price_parser import parse_price_ron
    text = text or ""
    curs = CURS.get(moneda, 1.0)
    brute = []

    if moneda == "EUR":
        for a, b in _RE_PRET_EUR.findall(text):
            brute.append(a or b)
    elif moneda == "USD":
        brute = _RE_PRET_USD.findall(text)
    else:
        brute = _RE_PRET_RO.findall(text)

    valori = []
    for brut in brute:
        v = parse_price_ron(str(brut) + " lei")
        if not v:
            continue
        v = v * curs
        if 5 <= v <= 50000:   # sub 5 lei sau peste 50k nu e produsul cautat
            valori.append(round(v, 2))
    return valori


def _cardul_se_potriveste(text_card: str, termen: str, prag: float = 0.6) -> bool:
    """
    Cardul asta e despre produsul cautat?

    Cerem ca cel putin `prag` din cuvintele cautarii (cele lungi, care poarta
    sensul) sa apara in card. Fara filtrul asta, o pagina de rezultate amesteca
    displayuri cu carti individuale si mediana devine inutilizabila.
    """
    from modules.classifier import _normalizeaza
    t = _normalizeaza(text_card)
    cuvinte = [c for c in _normalizeaza(termen).split() if len(c) > 2]
    if not cuvinte:
        return True
    gasite = sum(1 for c in cuvinte if c in t)
    return (gasite / len(cuvinte)) >= prag


def rezuma(valori: list, piata: str) -> dict:
    """
    Mediana curatata de extreme, plus discountul de realism.

    Taiem primul si ultimul sfert: pe OLX si Vinted extremele sunt anunturi
    gresite (o singura carte listata ca "booster box") sau licitatii aiurea.
    """
    if len(valori) < MIN_ESANTION:
        return {"ok": False, "motiv": f"doar {len(valori)} anunturi, prea putine"}

    v = sorted(valori)[:MAX_ESANTION]
    taiere = len(v) // 4
    mijloc = v[taiere:len(v) - taiere] or v

    median_brut = statistics.median(mijloc)
    discount = DISCOUNT_REALISM.get(piata, 0.85)

    return {
        "ok": True,
        "median_cereri": round(median_brut, 2),
        "median_estimat": round(median_brut * discount, 2),
        "esantion": len(mijloc),
        "discount_aplicat": discount,
        "min": mijloc[0],
        "max": mijloc[-1],
    }


# ─────────────────────────────────────────────────────────────────
#  Cautarea propriu-zisa, cu browser
# ─────────────────────────────────────────────────────────────────
PIETE = {
    # ── Piete RO: doar pentru produse care EXISTA deja aici ──────
    "olx": {
        "url": "https://www.olx.ro/oferte/q-{q}/",
        "asteapta": "[data-cy='l-card'], [data-testid='l-card']",
        "card": "[data-cy='l-card']",
        "profil": "olx_profile", "moneda": "RON", "pre_lansare": False,
    },
    "vinted": {
        "url": "https://www.vinted.ro/catalog?search_text={q}",
        "asteapta": "[data-testid*='item'], .feed-grid__item",
        "card": ".feed-grid__item",
        "profil": "vinted_profile", "moneda": "RON", "pre_lansare": False,
    },
    # ── Piete EU/US: singurele cu preturi pentru seturi NELANSATE ──
    # Cardmarket listeaza preorder cu saptamani/luni inainte de lansare, si
    # afiseaza preturi de vanzare efective, nu cereri. E singura sursa utila
    # pentru un set ca 30th Celebration, care in RO nici nu exista inca.
    "tcgplayer": {
        "url": "https://www.tcgplayer.com/search/all/product?q={q}&view=grid",
        "asteapta": "[class*='search-result'], .product-card",
        "card": ".product-card",
        "profil": "tcgplayer_profile", "moneda": "USD", "pre_lansare": True,
    },
    # CARDMARKET E BLOCAT. Testat pe 19 august 2026 cu browser real, profil
    # persistent, headless si vizibil: intoarce pagina Cloudflare
    # "Sorry, you have been blocked". Nu e selector gresit — e blocare la
    # nivel de retea. Ramane aici doar ca sa nu-l reincerce cineva degeaba.
    # Alternativa reala ar fi API-ul lor oficial, care cere aprobare de app.
    "cardmarket": {
        "url": "https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={q}",
        "asteapta": ".table-body, .product-row, table",
        "profil": "cardmarket_profile", "moneda": "EUR", "pre_lansare": True,
        "blocat": True,
    },
}

# Curs orientativ pentru conversie. Nu are nevoie de precizie — cifra finala e
# oricum marcata cu incredere mica.
CURS = {"RON": 1.0, "EUR": 5.05, "USD": 4.35}


def piata_recomandata(lanseaza_la: str = "", azi: date = None) -> str:
    """
    Ce piata are sens de intrebat pentru produsul asta.

    Un set care se lanseaza in septembrie nu are cum sa fie pe OLX in august —
    nu exista in Romania. Pentru el, singurul pret real e preorderul de pe
    Cardmarket. Dupa lansare, piata RO devine cea relevanta pentru revanzare.
    """
    if not lanseaza_la:
        return "olx"
    try:
        lansare = date.fromisoformat(str(lanseaza_la)[:10])
    except ValueError:
        return "olx"
    # Lasam 3 saptamani dupa lansare ca marfa sa ajunga efectiv pe piata RO.
    from datetime import timedelta
    return "tcgplayer" if (azi or date.today()) < lansare + timedelta(days=21) else "olx"


def cauta_pret(termen: str, piata: str = "olx", headless: bool = True,
               timeout_ms: int = 30000) -> dict:
    """
    O singura cautare pe o piata secundara. Intoarce rezumatul de preturi.

    Foloseste aceeasi configuratie anti-bot ca scraper.py. NU chema asta in
    bucla de scanare — e pentru rulari saptamanale sau la cerere.
    """
    config = PIETE.get(piata)
    if not config:
        return {"ok": False, "motiv": f"piata '{piata}' necunoscuta"}
    if config.get("blocat"):
        return {"ok": False, "motiv": f"{piata} blocheaza accesul (Cloudflare) — "
                                      "foloseste alta piata"}

    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    url = config["url"].format(q=quote(termen))
    profil = os.path.join(os.getcwd(), "config", "profiles", config["profil"])

    logging.info(f"💰 [Preturi] Caut '{termen}' pe {piata}...")

    with sync_playwright() as p:
        context = None
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profil,
                channel="msedge",
                headless=headless,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-infobars", "--window-position=-3000,0"],
            )
            pagina = context.pages[0] if context.pages else context.new_page()
            pagina.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                pagina.wait_for_selector(config["asteapta"], timeout=timeout_ms)
            except PWTimeout:
                # Prima vizita pe un profil nou are adesea un banner de
                # consimtamant care acopera rezultatele. Se rezolva o singura
                # data, manual, cu --vizibil.
                return {"ok": False, "motiv": "pagina nu a incarcat rezultate "
                        "(prima vizita? ruleaza cu --vizibil si accepta manual "
                        "bannerul de cookies — profilul il tine minte)"}
            pagina.wait_for_timeout(2500)

            # Citim FIECARE CARD separat, nu tot textul paginii. Altfel
            # amestecam preturi de la produse complet diferite: pe TCGplayer
            # a iesit un interval 340-1762 lei, adica singles langa displayuri.
            texte = []
            selector_card = config.get("card")
            if selector_card:
                carduri = pagina.locator(selector_card)
                for i in range(min(carduri.count(), 40)):
                    try:
                        texte.append(carduri.nth(i).inner_text())
                    except Exception:
                        continue
            if not texte:
                texte = [pagina.locator("body").inner_text()]
        except Exception as e:
            return {"ok": False, "motiv": f"eroare browser: {e}"}
        finally:
            if context:
                context.close()

    moneda = config.get("moneda", "RON")
    valori = []
    respinse = 0
    detalii = []
    for text_card in texte:
        # Cardul trebuie sa fie despre ce am cautat. Un card care nu contine
        # cuvintele-cheie e alt produs, iar pretul lui strica mediana.
        if not _cardul_se_potriveste(text_card, termen):
            respinse += 1
            continue
        preturi_card = extrage_preturi(text_card, moneda)
        valori.extend(preturi_card)
        if preturi_card:
            titlu = " ".join(text_card.split())[:70]
            detalii.append({"titlu": titlu, "preturi": sorted(preturi_card)})

    rezumat = rezuma(valori, piata)
    rezumat["carduri_citite"] = len(texte)
    rezumat["carduri_respinse"] = respinse
    rezumat["detalii"] = detalii
    rezumat["piata"] = piata
    rezumat["termen"] = termen
    return rezumat


def actualizeaza_pret(id_canonic: str, termen: str, piata: str = "olx",
                      headless: bool = True) -> dict:
    """Cauta si salveaza intr-un singur pas."""
    r = cauta_pret(termen, piata, headless=headless)
    if r.get("ok"):
        inregistreaza_pret(id_canonic, r["median_estimat"], piata,
                           r["esantion"], incredere="mica")
        logging.info(
            f"💰 [Preturi] {id_canonic}: ~{r['median_estimat']:.0f} lei "
            f"(din {r['esantion']} anunturi pe {piata})"
        )
    else:
        logging.info(f"💰 [Preturi] {id_canonic}: {r.get('motiv')}")
    return r


# ─────────────────────────────────────────────────────────────────
#  Brief-ul de sub alerta
# ─────────────────────────────────────────────────────────────────
def brief(id_canonic: str, pret_magazin: float = None) -> str:
    """
    Text scurt despre ce stim despre pretul produsului. Gol daca nu stim nimic.

    Se trimite DUPA alerta, ca mesaj separat — alerta nu are voie sa astepte
    dupa nimic. Un produs bun intra o singura data.
    """
    intrare = pret_cunoscut(id_canonic)
    if not intrare:
        return ""

    median = intrare.get("median_ron")
    if not median:
        return ""

    zile = vechime_zile(id_canonic)
    vechime = "azi" if zile == 0 else (f"acum {zile} zile" if zile else "necunoscut")

    linii = [
        f"📊 <b>Ce stim despre pret</b>",
        f"Piata secundara: ~{median:.0f} lei ({intrare.get('sursa', '?')}, "
        f"{intrare.get('esantion', '?')} anunturi, verificat {vechime})",
    ]

    tendinta = intrare.get("tendinta_pct")
    if tendinta:
        sageata = "📈" if tendinta > 0 else "📉"
        linii.append(f"{sageata} Fata de verificarea anterioara: {tendinta:+.1f}%")

    if pret_magazin and pret_magazin > 0:
        brut = median - pret_magazin
        linii.append(f"Diferenta bruta fata de {pret_magazin:.0f} lei: {brut:+.0f} lei")

    linii.append("<i>Atentie: sunt CERERI de pe piata, nu vanzari confirmate. "
                 "Cifra e orientativa, nu decizie de cumparare.</i>")
    return "\n".join(linii)


def raport() -> str:
    """Text pentru comanda /preturi."""
    with _lock:
        carte = dict(_incarca())
    if not carte:
        return ("📊 Registrul de preturi e gol.\n\n"
                "Ruleaza <code>python tools/verifica_preturi.py</code> ca sa-l populezi.")

    linii = [f"📊 <b>Registru preturi</b> — {len(carte)} produse\n"]
    for id_canonic, d in sorted(carte.items())[:20]:
        zile = vechime_zile(id_canonic)
        t = d.get("tendinta_pct")
        trend = f" {'📈' if t > 0 else '📉'}{t:+.0f}%" if t else ""
        linii.append(f"<code>{id_canonic}</code>")
        linii.append(f"   ~{d.get('median_ron', 0):.0f} lei · {d.get('sursa', '?')} · "
                     f"{zile if zile is not None else '?'} zile{trend}")
    return "\n".join(linii)
