import json
import time
from modules.scraper import check_search_page_stock
from modules.notifier import send_telegram_notification

# --- CONFIGURARE ---
DEBUG_MODE = False  # Schimbă în True dacă vrei să re-trimiți tot pe Telegram pentru test
CHECK_INTERVAL = 300 
# --------------------

def load_json_list(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def main():
    print("\n" + "="*50)
    print(" 🔥 POKEMON STOCK MONITOR V3.0 - MULTI-USER & DEBUG 🔥")
    print("="*50 + "\n")
    
    known_products = {} 

    while True:
        try:
            sites = load_json_list("config/sites_config.json")
            vip_keywords = load_json_list("config/vip_keywords.json")
            blacklist_keywords = load_json_list("config/blacklist_keywords.json")

            for site in sites:
                site_name = site["name"]
                found_products = check_search_page_stock(site)
                
                if site_name not in known_products:
                    known_products[site_name] = set()
                    # Dacă nu e DEBUG, memorăm starea actuală fără să trimitem (Silent Start)
                    if not DEBUG_MODE:
                        for p in found_products:
                            p_name_lower = p["name"].strip().lower()
                            is_vip = any(v.lower() in p_name_lower for v in vip_keywords if v.strip())
                            is_black = any(b.lower() in p_name_lower for b in blacklist_keywords if b.strip())
                            if is_black and not is_vip: continue
                            known_products[site_name].add(p_name_lower)
                        print(f"🤫 [SILENT START] {site_name}: Am memorat produsele existente.")
                        continue

                valid_count = 0
                for p in found_products:
                    p_name = p["name"]
                    p_name_lower = p_name.strip().lower()
                    p_url = p["url"]
                    p_img = p["image"]
                    p_price = p["price"]

                    is_vip = any(v.lower() in p_name_lower for v in vip_keywords if v.strip())
                    is_black = any(b.lower() in p_name_lower for b in blacklist_keywords if b.strip())

                    if is_black and not is_vip:
                        continue 

                    valid_count += 1

                    # Trimitem dacă e produs nou SAU dacă suntem în modul Debug
                    if DEBUG_MODE or (p_name_lower not in known_products[site_name]):
                        status = "💎 [VIP]" if is_vip else "✨ [NOU]"
                        print(f"{status} {site_name} -> {p_name} ({p_price})")
                        
                        send_telegram_notification(p_name, p_url, p_price, site_name, p_img, is_vip)
                        
                        # Adăugăm în memorie doar dacă nu suntem în debug (ca să nu spamăm la infinit în debug)
                        if not DEBUG_MODE:
                            known_products[site_name].add(p_name_lower)
                        time.sleep(1.5) 

                print(f"📊 [{site_name}] Scanare completă: {valid_count} produse valide.")

            print(f"\n⏳ Pauză {CHECK_INTERVAL//60} min...")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n🛑 Monitor oprit manual.")
            break
        except Exception as e:
            print(f"❌ Eroare critică: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()