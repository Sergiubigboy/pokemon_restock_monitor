import os
import random
import time
from playwright.sync_api import sync_playwright

def check_search_page_stock(site_config: dict) -> list:
    url = site_config.get("url")
    name = site_config.get("name")
    card_selector = site_config.get("card_selector", ".product-item")
    title_selector = site_config.get("title_selector", ".product-item-name")
    price_selector = site_config.get("price_selector", ".price") # <--- NOU
    in_stock_text = site_config.get("in_stock_text", "").lower()
    
    profile_folder = site_config.get("profile_folder", "default_profile")
    user_data_dir = os.path.join(os.getcwd(), "config", "profiles", profile_folder)

    available_products = []

    print(f"🔍 Scanam: {name}...")

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="msedge", 
                headless=True, 
                viewport={"width": 1280, "height": 720},
                args=["--disable-blink-features=AutomationControlled", "--disable-infobars", "--window-position=-3000,0"]
            )
            
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            try:
                page.wait_for_selector(card_selector, timeout=15000)
            except Exception:
                print(f"⚠️ EROARE la {name}: Timpul a expirat (15 sec).")
                return []
            
            page.wait_for_timeout(2000)
            product_cards = page.locator(card_selector).all()
            print(f"  -> Debug: Extras {len(product_cards)} carduri din HTML.")

            for card in product_cards:
                text_card = card.text_content() or ""
                
                if not in_stock_text or in_stock_text in text_card.lower():
                    
                    title_el = card.locator(title_selector).first
                    p_name = title_el.text_content().strip() if title_el else "Necunoscut"
                    
                    # --- NOU: Extragem PREȚUL ---
                    price_el = card.locator(price_selector).first
                    p_price = price_el.text_content().strip() if price_el else "N/A"
                    p_price = " ".join(p_price.split()) # Curățăm textul de spații aiurea
                    
                    link_el = card.locator("a").first
                    p_link = link_el.get_attribute("href") if link_el else url
                    
                    if p_link and p_link.startswith("/"):
                        from urllib.parse import urlparse
                        parsed = urlparse(url)
                        base_domain = f"{parsed.scheme}://{parsed.netloc}"
                        p_link = base_domain + p_link
                        
                    img_el = card.locator("img").first
                    p_img = img_el.get_attribute("src") if img_el else None
                    
                    if p_img and p_img.startswith("//"):
                        p_img = "https:" + p_img

                    available_products.append({
                        "name": p_name,
                        "url": p_link,
                        "image": p_img,
                        "price": p_price # <--- Adăugat aici
                    })

        except Exception as e:
            print(f"⚠️ Eroare generală la {name}: {e}")
        finally:
            if 'context' in locals(): context.close()

    return available_products