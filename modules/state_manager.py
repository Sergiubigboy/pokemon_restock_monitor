import json
import os

KNOWN_PRODUCTS_FILE = os.path.join("config", "known_products.json")
MUTED_SITES_FILE    = os.path.join("config", "muted_sites.json")

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
    data = {site: sorted(list(products)) for site, products in known_products.items()}
    try:
        with open(KNOWN_PRODUCTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ [StateManager] Nu am putut salva known_products: {e}")

def add_product(known_products: dict, site_name: str, product_name_lower: str):
    """Adaugă un produs în starea cunoscută și o salvează pe disk."""
    if site_name not in known_products:
        known_products[site_name] = set()
    known_products[site_name].add(product_name_lower)
    save_known_products(known_products)

def remove_stale_products(known_products: dict, site_name: str, current_valid_names: set):
    """
    Scoate din known_products produsele care nu mai apar pe site.
    Returnează setul de produse șterse (pentru logging).
    """
    if site_name not in known_products:
        return set()

    stale = known_products[site_name] - current_valid_names
    if stale:
        known_products[site_name] = known_products[site_name] & current_valid_names
        save_known_products(known_products)
        print(f"🗑️  [{site_name}] Produse scoase din JSON (dispărute): {len(stale)}")
        for p in stale:
            print(f"   - {p}")
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
        with open(MUTED_SITES_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(muted)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ [StateManager] Nu am putut salva muted_sites: {e}")
