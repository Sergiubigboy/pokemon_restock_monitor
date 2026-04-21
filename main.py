import json
import time
from modules.scraper import check_search_page_stock
from modules.notifier import send_telegram_notification

CHECK_INTERVAL = 300 

def load_json_list(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def main():
    print("\n" + "="*50)
    print(" 🔥 POKEMON STOCK MONITOR V2.2 - ANTI-SPAM & PRICES 🔥")
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
                
                # START SILENȚIOS
                if site_name not in known_products:
                    known_products[site_name] = set()
                    valid_count = 0
                    
                    for p in found_products:
                        is_vip = any(v.lower() in p["name"].lower() for v in vip_keywords if v.strip())
                        is_blacklisted = any(b.lower() in p["name"].lower() for b in blacklist_keywords if b.strip())
                        
                        if is_blacklisted and not is_vip:
                            continue
                        
                        # --- NOU: Memorăm NUMELE (în litere mici), nu URL-ul ---
                        known_products[site_name].add(p["name"].strip().lower())
                        valid_count += 1
                        
                    print(f"🤫 [SILENT START] {site_name}: Am memorat {valid_count} produse valide existente. Nu trimit spam.")
                    continue 

                # VERIFICAREA NORMALĂ
                valid_products = 0
                for p in found_products:
                    p_url = p["url"]
                    p_name = p["name"]
                    p_img = p["image"]
                    p_price = p["price"] # NOU
                    
                    p_name_lower = p_name.strip().lower()

                    is_vip = any(v.lower() in p_name_lower for v in vip_keywords if v.strip())
                    is_blacklisted = any(b.lower() in p_name_lower for b in blacklist_keywords if b.strip())

                    if is_blacklisted and not is_vip:
                        continue 

                    valid_products += 1

                    # --- NOU: Verificăm dacă NUMELE este în memoria noastră ---
                    if p_name_lower not in known_products[site_name]:
                        prefix = "💎 [VIP] " if is_vip else "✨ [NOU] "
                        print(f"{prefix}Detectat și trimis pe Telegram: {p_name} | {p_price}")
                        
                        send_telegram_notification(p_name, p_url, p_price, p_img, is_vip)
                        
                        # Adăugăm numele în memorie
                        known_products[site_name].add(p_name_lower)
                        time.sleep(1.5) 

                print(f"📊 [{site_name}] Scanare completă. {valid_products} produse valide se află acum în stoc.")

            print(f"\n⏳ Pauză {CHECK_INTERVAL//60} min...")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n🛑 Monitor oprit manual.")
            break
        except Exception as e:
            print(f"❌ Eroare în bucla principală: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()