import os
import time
import logging
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from modules.stock_parser import parse_stock_qty

# Canalul care a mers ultima data. Fara memoria asta, pe un Pi fara Edge am
# reincerca msedge la fiecare magazin, la fiecare ciclu — 8 esecuri pe secunda.
_canal_functional = None


def _canale_disponibile(site_config: dict) -> list:
    """
    Canalele de incercat, in ordine. Primul care porneste castiga.

    Pe Pi (ARM64) Edge nu exista si nu va exista — Microsoft nu-l livreaza
    pentru arhitectura asta. Ca sa nu ghicim la fiecare pornire, poti forta
    canalul din config/.env:

        PLAYWRIGHT_CHANNEL=            (gol = Chromium livrat de Playwright)
        PLAYWRIGHT_CHANNEL=chromium    (Chromium instalat in sistem)
        PLAYWRIGHT_CHANNEL=msedge      (implicit pe Windows)

    Variabila setata, chiar si goala, opreste orice incercare de fallback.
    """
    global _canal_functional

    fortat = os.getenv("PLAYWRIGHT_CHANNEL")
    if fortat is not None:
        return [fortat.strip() or None]

    if _canal_functional is not None:
        return [_canal_functional]

    preferat = site_config.get("browser_channel", "msedge")
    return [preferat, "chromium", None]


def check_search_page_stock(site_config: dict) -> list:
    # ── Fast-path fara browser ────────────────────────────────────────
    # Activat per site cu "engine": "http" in sites_config.json.
    # Fara flagul asta nu se schimba NIMIC — toata logica anti-bot de mai jos
    # (persistent context, msedge, AutomationControlled) ramane neatinsa.
    # Importul e local intentionat: fara flag, bs4 nici nu trebuie instalat.
    if site_config.get("engine") == "http":
        from modules.http_scraper import check_search_page_stock_http
        return check_search_page_stock_http(site_config)

    url = site_config.get("url")
    name = site_config.get("name")
    card_selector = site_config.get("card_selector", ".product-item")
    title_selector = site_config.get("title_selector", ".product-item-name")
    price_selector = site_config.get("price_selector", ".price")
    image_selector = site_config.get("image_selector", "img")
    in_stock_text = site_config.get("in_stock_text", "").lower()
    
    # --- Citim setarea de headless. Dacă nu există, default e True ---
    is_headless = site_config.get("headless", True) 
    
    profile_folder = site_config.get("profile_folder", "default_profile")
    user_data_dir = os.path.join(os.getcwd(), "config", "profiles", profile_folder)

    available_products = []

    logging.info(f"🔍 Scanam: {name}...")

    with sync_playwright() as p:
        context = None
        try:
            # Canalul de browser. msedge e prima alegere — profilurile
            # persistente si comportamentul anti-bot sunt calibrate pe el.
            # DAR pe Raspberry Pi (ARM64) Edge nu exista: Microsoft nu
            # livreaza Edge pentru arhitectura asta. Acolo cadem pe Chromium-ul
            # livrat de Playwright, care ruleaza nativ pe ARM.
            # Fara fallback, TOATE magazinele pe browser dau 0 produse pe Pi.
            canale = _canale_disponibile(site_config)
            ultima_eroare = None
            context = None
            for canal in canale:
                try:
                    optiuni = {
                        "user_data_dir": user_data_dir,
                        "headless": is_headless,
                        "viewport": {"width": 1280, "height": 720},
                        "args": ["--disable-blink-features=AutomationControlled",
                                 "--disable-infobars", "--window-position=-3000,0"],
                    }
                    if canal:
                        optiuni["channel"] = canal
                    context = p.chromium.launch_persistent_context(**optiuni)
                    global _canal_functional
                    if _canal_functional is None and canal != canale[0]:
                        logging.warning(
                            f"⚠️ Canalul '{canale[0]}' nu e disponibil pe masina asta. "
                            f"Trec pe '{canal or 'chromium'}' pentru toate magazinele."
                        )
                    _canal_functional = canal
                    break
                except Exception as e:
                    ultima_eroare = e
                    continue

            if context is None:
                raise ultima_eroare or RuntimeError("niciun canal de browser disponibil")
            
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            try:
                # Am crescut timeout-ul la 30s pentru site-urile mai lente / React
                page.wait_for_selector(card_selector, timeout=30000)
            except PlaywrightTimeoutError:
                logging.warning(f"⚠️ EROARE la {name}: Timpul a expirat (30 sec). Posibil stoc 0 sau pagină lentă.")
                return []
            
            page.wait_for_timeout(2000)
            product_cards = page.locator(card_selector).all()
            logging.debug(f"Extras {len(product_cards)} carduri din HTML pentru {name}.")

            for card in product_cards:
                text_card = card.text_content() or ""
                
                if not in_stock_text or in_stock_text in text_card.lower():
                    
                    title_el = card.locator(title_selector).first
                    p_name = title_el.text_content().strip() if title_el else "Necunoscut"
                    
                    # --- Extragem PREȚUL ---
                    price_el = card.locator(price_selector).first
                    p_price = price_el.text_content().strip() if price_el else "N/A"
                    p_price = " ".join(p_price.split()) # Curățăm textul de spații aiurea
                    
                    # Daca site-ul are "link_selector" in config, il folosim.
                    # Altfel ramane comportamentul vechi: primul <a> din card.
                    link_selector = site_config.get("link_selector")
                    p_link = None
                    if link_selector:
                        try:
                            p_link = card.locator(link_selector).first.get_attribute("href")
                        except Exception:
                            p_link = None
                    if not p_link:
                        link_el = card.locator("a").first
                        p_link = link_el.get_attribute("href") if link_el else url
                    
                    # Reparăm link-urile relative (ex: Europosters, Smyk)
                    if p_link and p_link.startswith("/"):
                        parsed = urlparse(url)
                        base_domain = f"{parsed.scheme}://{parsed.netloc}"
                        p_link = base_domain + p_link
                        
                    img_el = card.locator(image_selector).first
                    p_img = img_el.get_attribute("src") if img_el else None
                    
                    if p_img and p_img.startswith("//"):
                        p_img = "https:" + p_img

                    # --- Extragem CANTITATEA (optional) ---
                    # Intai selectorul dedicat "qty_selector", daca site-ul are
                    # unul, apoi cautam un numar in textul intregului card.
                    # None inseamna "necunoscuta" si alerta se trimite fara ea.
                    text_stoc = ""
                    qty_selector = site_config.get("qty_selector")
                    if qty_selector:
                        try:
                            text_stoc = card.locator(qty_selector).first.text_content() or ""
                        except Exception:
                            text_stoc = ""
                    p_qty = parse_stock_qty(text_stoc) or parse_stock_qty(text_card)

                    available_products.append({
                        "name": p_name,
                        "url": p_link,
                        "image": p_img,
                        "price": p_price,
                        "qty": p_qty
                    })

        except Exception as e:
            logging.error(f"⚠️ Eroare generală la {name}: {e}")
        finally:
            # --- CURĂȚAREA MEMORIEI (OBLIGATORIU PENTRU LINUX) ---
            if context: 
                context.close()

    return available_products