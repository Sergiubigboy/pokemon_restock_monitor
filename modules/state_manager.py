import json
import logging
import os
import threading
import time

KNOWN_PRODUCTS_FILE = os.path.join("config", "known_products.json")
MUTED_SITES_FILE    = os.path.join("config", "muted_sites.json")
HISTORICAL_PRODUCTS_FILE = os.path.join("config", "historical_products.json")
ABSENCE_FILE        = os.path.join("config", "product_absence.json")
LAST_NOTIFIED_FILE  = os.path.join("config", "last_notified.json")

# De câte scanări reușite consecutive trebuie să lipsească un produs ca să fie
# considerat cu adevărat dispărut. 3 acoperă oscilațiile obișnuite ale
# magazinelor fără să întârzie prea mult curățarea reală.
MIN_ABSENTE_PENTRU_STERGERE = 3

# Cât timp nu renotificăm același produs de pe același magazin, chiar dacă
# ajunge cumva să fie marcat ca "nou" din nou. Ultima plasă de siguranță.
RACIRE_RENOTIFICARE_SECUNDE = 6 * 3600

# Intrările mai vechi de atât se aruncă la salvare, ca fișierul să nu crească
# la nesfârșit pe un bot care rulează luni întregi.
VECHIME_MAXIMA_NOTIFICARI = 7 * 24 * 3600

# De când scanăm mai multe nișe în paralel, două thread-uri pot salva starea
# în același timp. Fără lock, un fișier ar fi scris peste altul la jumătate și
# ar rămâne JSON corupt — adică istoricul pierdut și toate produsele
# renotificate ca "noi" la următoarea pornire.
_lock_stare = threading.RLock()


def _scrie_json_atomic(cale: str, date):
    """
    Scrie într-un fișier temporar și abia apoi îl mută peste cel real.
    os.replace e atomic, deci o cădere de curent pe Pi nu poate lăsa în urmă
    un JSON trunchiat.
    """
    temporar = cale + ".tmp"
    director = os.path.dirname(cale)
    if director:
        os.makedirs(director, exist_ok=True)
    with open(temporar, "w", encoding="utf-8") as f:
        json.dump(date, f, ensure_ascii=False, indent=2)

    # Pe Windows, os.replace poate esua cu WinError 32 daca antivirusul sau
    # indexatorul tin fisierul .tmp deschis o fractiune de secunda. Nu e o
    # eroare reala — reincercam scurt inainte sa renuntam.
    for incercare in range(5):
        try:
            os.replace(temporar, cale)
            return
        except PermissionError:
            if incercare == 4:
                raise
            time.sleep(0.1 * (incercare + 1))

def load_historical_products() -> dict:
    try:
        with open(HISTORICAL_PRODUCTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_historical_products(data: dict):
    try:
        with _lock_stare:
            _scrie_json_atomic(HISTORICAL_PRODUCTS_FILE, data)
    except Exception as e:
        logging.warning(f"⚠️ [StateManager] Nu am putut salva historical_products: {e}")

def log_product_appearance(site_name: str, product_name: str):
    # Citire + modificare + scriere trebuie să fie o singură operație atomică,
    # altfel două nișe paralele își pierd reciproc înregistrările.
    with _lock_stare:
        data = load_historical_products()
        if site_name not in data:
            data[site_name] = []

        for item in data[site_name]:
            if item['name'] == product_name and item['disappeared_at'] is None:
                return

        data[site_name].append({
            "name": product_name,
            "appeared_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "disappeared_at": None
        })
        save_historical_products(data)

def log_product_disappearance(site_name: str, product_names: set):
    with _lock_stare:
        data = load_historical_products()
        if site_name not in data:
            return

        changed = False
        for p in product_names:
            for item in data[site_name]:
                if item['name'] == p and item['disappeared_at'] is None:
                    item['disappeared_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
                    changed = True
                    break

        if changed:
            save_historical_products(data)

# ─────────────────────────────────────────────────────────────────
#  Contor de absențe — inima protecției anti-duplicat
# ─────────────────────────────────────────────────────────────────
def _load_absente() -> dict:
    try:
        with open(ABSENCE_FILE, "r", encoding="utf-8") as f:
            date = json.load(f)
        return date if isinstance(date, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logging.warning(f"⚠️ [StateManager] Nu am putut citi contorul de absențe: {e}")
        return {}


def _save_absente(absente: dict):
    try:
        # Curățăm site-urile fără nicio absență, ca fișierul să rămână mic.
        curatat = {site: d for site, d in absente.items() if d}
        _scrie_json_atomic(ABSENCE_FILE, curatat)
    except Exception as e:
        logging.warning(f"⚠️ [StateManager] Nu am putut salva contorul de absențe: {e}")


# ─────────────────────────────────────────────────────────────────
#  Răcire la renotificare — plasa de siguranță
# ─────────────────────────────────────────────────────────────────
def _load_notificari() -> dict:
    try:
        with open(LAST_NOTIFIED_FILE, "r", encoding="utf-8") as f:
            date = json.load(f)
        return date if isinstance(date, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception:
        return {}


def poate_notifica(site_name: str, product_name: str,
                   racire: int = RACIRE_RENOTIFICARE_SECUNDE) -> bool:
    """
    False dacă produsul ăsta a fost deja notificat recent de pe același magazin.

    Contorul de absențe rezolvă cauza duplicatelor; asta e ultima plasă, pentru
    cazurile în care starea se pierde oricum (JSON corupt, restart, `git pull`
    care șterge known_products.json).
    """
    if racire <= 0:
        return True
    cheie = f"{site_name}||{product_name}"
    with _lock_stare:
        ultima = _load_notificari().get(cheie, 0)
    return (time.time() - float(ultima)) >= racire


def marcheaza_notificat(site_name: str, product_name: str):
    """De chemat imediat după ce notificarea a plecat efectiv."""
    cheie = f"{site_name}||{product_name}"
    acum = time.time()
    with _lock_stare:
        try:
            date = _load_notificari()
            date[cheie] = acum
            # Aruncăm intrările vechi ca fișierul să nu crească la nesfârșit.
            limita = acum - VECHIME_MAXIMA_NOTIFICARI
            date = {k: v for k, v in date.items() if float(v) >= limita}
            _scrie_json_atomic(LAST_NOTIFIED_FILE, date)
        except Exception as e:
            logging.warning(f"⚠️ [StateManager] Nu am putut salva marcajul de notificare: {e}")


def load_known_products() -> dict:
    """
    Încarcă produsele cunoscute din fișierul JSON persistent.
    Returnează un dict: { "site_name": ["produs 1", "produs 2", ...] }
    """
    try:
        with open(KNOWN_PRODUCTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Convertim listele în seturi pentru căutare rapidă
            return {site: set(products) for site, products in data.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_known_products(known_products: dict):
    """
    Salvează produsele cunoscute pe disk.
    Convertim seturile înapoi în liste sortate pentru JSON ușor de citit.
    """
    try:
        with _lock_stare:
            data = {site: sorted(list(products)) for site, products in known_products.items()}
            _scrie_json_atomic(KNOWN_PRODUCTS_FILE, data)
    except Exception as e:
        logging.warning(f"⚠️ [StateManager] Nu am putut salva known_products: {e}")

def add_product(known_products: dict, site_name: str, product_name_lower: str):
    """Adaugă un produs în starea cunoscută și o salvează pe disk."""
    with _lock_stare:
        if site_name not in known_products:
            known_products[site_name] = set()
        known_products[site_name].add(product_name_lower)
        save_known_products(known_products)
    log_product_appearance(site_name, product_name_lower)

def remove_stale_products(known_products: dict, site_name: str, current_valid_names: set,
                          min_absente: int = MIN_ABSENTE_PENTRU_STERGERE):
    """
    Scoate din known_products produsele care lipsesc de mai multe scanări la rând.

    ATENȚIE — de ce nu ștergem la prima absență:
    Magazinele oscilează. PokeMANIA sau Noriel întorc uneori 3 produse din 20
    (randare parțială, pagină lentă, paginare schimbată). Dacă am șterge la
    prima absență, celelalte 17 ar fi purjate și renotificate ca "produse noi"
    la ciclul următor. Măsurat pe feed-ul real: 48% din notificări erau
    duplicate din cauza asta.

    Un produs se scoate doar după ce lipsește din `min_absente` scanări reușite
    CONSECUTIV. O singură reapariție resetează contorul.
    """
    with _lock_stare:
        if site_name not in known_products:
            return set()

        absente = _load_absente()
        absente_site = absente.setdefault(site_name, {})

        # Produsele revăzute își resetează contorul.
        for nume in current_valid_names:
            absente_site.pop(nume, None)

        lipsesc_acum = known_products[site_name] - current_valid_names
        stale = set()
        for nume in lipsesc_acum:
            absente_site[nume] = absente_site.get(nume, 0) + 1
            if absente_site[nume] >= min_absente:
                stale.add(nume)

        if stale:
            known_products[site_name] = known_products[site_name] - stale
            for nume in stale:
                absente_site.pop(nume, None)
            save_known_products(known_products)

        _save_absente(absente)

    if stale:
        log_product_disappearance(site_name, stale)
        logging.info(f"🗑️  [{site_name}] Produse scoase din JSON (lipsă de {min_absente} scanări): {len(stale)}")
        for p in stale:
            logging.info(f"   - {p}")
    return stale

# ─────────────────────────────────────────────────────────────────
#  Muted Sites
# ─────────────────────────────────────────────────────────────────
def load_muted_sites() -> set:
    """ncărcă lista de site-uri mute din fișierul JSON."""
    try:
        with open(MUTED_SITES_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_muted_sites(muted: set):
    """Salvează lista de site-uri mute pe disk."""
    try:
        with _lock_stare:
            _scrie_json_atomic(MUTED_SITES_FILE, sorted(list(muted)))
    except Exception as e:
        logging.warning(f"⚠️ [StateManager] Nu am putut salva muted_sites: {e}")
