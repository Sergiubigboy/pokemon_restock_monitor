import os
import time
import logging
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

def check_search_page_stock(site_config: dict) -> list:
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
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="msedge", 
                headless=is_headless, # <--- Aici aplicăm setarea dinamică
                viewport={"width": 1280, "height": 720},
                args=["--disable-blink-features=AutomationControlled", "--disable-infobars", "--window-position=-3000,0"]
            )
            
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

                    available_products.append({
                        "name": p_name,
                        "url": p_link,
                        "image": p_img,
                        "price": p_price
                    })

        except Exception as e:
            logging.error(f"⚠️ Eroare generală la {name}: {e}")
        finally:
            # --- CURĂȚAREA MEMORIEI (OBLIGATORIU PENTRU LINUX) ---
            if context: 
                context.close()

    return available_products