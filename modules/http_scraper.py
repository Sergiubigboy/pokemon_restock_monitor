"""
Fast-path fara browser, pentru magazinele care servesc HTML gata randat.

Se activeaza per site, cu "engine": "http" in sites_config.json. Fara flagul
asta nu se schimba absolut nimic — site-ul merge pe calea Playwright existenta.

De ce merita: fiecare scanare Playwright porneste un Edge complet (~250-400 MB
rezidenti). Pe un Pi cu 4GB, mutarea catorva magazine simple pe HTTP elibereaza
memoria de care ai nevoie ca sa rulezi doua nise in paralel.

Ce NU face: nu executa JavaScript si nu are niciun mecanism anti-bot. Site-urile
care randeaza produsele din JS sau au Cloudflare TREBUIE lasate pe Playwright.
Foloseste tools/test_http_engine.py ca sa verifici inainte de a comuta un site.

Selectorii sunt aceiasi (CSS), deci un site se muta schimband o singura cheie.
"""

import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from modules.stock_parser import parse_stock_qty

# Antet realist de browser. Nu e evaziune — e minimul ca serverul sa raspunda
# cu acelasi HTML pe care l-ar da unui vizitator obisnuit.
ANTET_IMPLICIT = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

TIMEOUT_IMPLICIT = 20


def _text(element) -> str:
    """Textul unui element bs4, cu spatiile normalizate."""
    if element is None:
        return ""
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()


def _primul(card, selector: str):
    """Echivalentul lui locator(sel).first din Playwright."""
    if not selector:
        return None
    try:
        return card.select_one(selector)
    except Exception:
        # Selector CSS invalid — il tratam ca "negasit", nu oprim scanarea.
        return None


# Href-uri care exista in card dar nu duc nicaieri util (wishlist, compara,
# adauga in cos). Un link "/" intr-o alerta de cumparare e inutil.
_HREF_INUTILE = ("", "/", "#", "javascript:void(0)", "javascript:;")


def _extrage_link(card, link_selector: str | None) -> str | None:
    """
    Linkul produsului. Daca site-ul are "link_selector" in config, il folosim;
    altfel luam primul <a> cu un href care chiar duce undeva.

    Redgoblin, de exemplu, are un <a> de wishlist inaintea celui de produs —
    fara filtrul asta, alerta ar contine linkul catre pagina principala.
    """
    if link_selector:
        element = _primul(card, link_selector)
        if element is not None and element.get("href"):
            return element.get("href")

    for ancora in card.select("a"):
        href = (ancora.get("href") or "").strip()
        if href.lower() in _HREF_INUTILE or href.lower().startswith("javascript:"):
            continue
        return href

    return None


def check_search_page_stock_http(site_config: dict) -> list:
    """
    Aceeasi semnatura si acelasi format de iesire ca
    scraper.check_search_page_stock: lista de dict-uri
    {"name", "url", "image", "price", "qty"}.
    """
    url = site_config.get("url")
    name = site_config.get("name")
    card_selector = site_config.get("card_selector", ".product-item")
    title_selector = site_config.get("title_selector", ".product-item-name")
    price_selector = site_config.get("price_selector", ".price")
    image_selector = site_config.get("image_selector", "img")
    qty_selector = site_config.get("qty_selector")
    in_stock_text = site_config.get("in_stock_text", "").lower()
    timeout = site_config.get("http_timeout", TIMEOUT_IMPLICIT)

    antet = dict(ANTET_IMPLICIT)
    antet.update(site_config.get("http_headers") or {})

    produse_disponibile = []

    logging.info(f"⚡ Scanam (HTTP, fara browser): {name}...")

    try:
        raspuns = requests.get(url, headers=antet, timeout=timeout)
    except requests.RequestException as e:
        logging.error(f"⚠️ Eroare HTTP la {name}: {e}")
        return []

    if raspuns.status_code != 200:
        logging.warning(
            f"⚠️ [{name}] HTTP {raspuns.status_code} — site-ul respinge cererile simple. "
            f"Probabil are nevoie de Playwright (scoate \"engine\": \"http\")."
        )
        return []

    try:
        supa = BeautifulSoup(raspuns.text, "html.parser")
        carduri = supa.select(card_selector)
    except Exception as e:
        logging.error(f"⚠️ Eroare la parsarea HTML pentru {name}: {e}")
        return []

    logging.debug(f"Extras {len(carduri)} carduri din HTML pentru {name}.")

    if not carduri:
        logging.warning(
            f"⚠️ [{name}] 0 carduri pe calea HTTP. Fie selectorul e gresit, fie "
            f"produsele sunt randate din JavaScript — atunci site-ul trebuie sa ramana pe Playwright."
        )
        return []

    for card in carduri:
        text_card = _text(card)

        if in_stock_text and in_stock_text not in text_card.lower():
            continue

        p_name = _text(_primul(card, title_selector)) or "Necunoscut"
        p_price = _text(_primul(card, price_selector)) or "N/A"

        p_link = _extrage_link(card, site_config.get("link_selector"))
        # urljoin rezolva si "/produs", si "produs", si "//cdn/..."
        p_link = urljoin(url, p_link) if p_link else url

        img_el = _primul(card, image_selector)
        p_img = None
        if img_el is not None:
            # Multe teme pun poza reala in data-src si lasa src pe un placeholder.
            for atribut in ("src", "data-src", "data-original", "data-lazy-src"):
                valoare = img_el.get(atribut)
                if valoare:
                    p_img = valoare
                    break
            if p_img:
                if p_img.startswith("//"):
                    p_img = "https:" + p_img
                elif p_img.startswith("/"):
                    p_img = urljoin(url, p_img)

        # Cantitatea: intai selectorul dedicat, apoi textul intregului card.
        text_stoc = _text(_primul(card, qty_selector)) if qty_selector else ""
        p_qty = parse_stock_qty(text_stoc) or parse_stock_qty(text_card)

        produse_disponibile.append({
            "name": p_name,
            "url": p_link,
            "image": p_img,
            "price": p_price,
            "qty": p_qty,
        })

    return produse_disponibile
