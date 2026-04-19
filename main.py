import json
import time
from modules.scraper import check_stock_status
from modules.notifier import send_telegram_notification

CONFIG_FILE = "config/sites_config.json"
CHECK_INTERVAL = 300  # 5 minute în secunde (poți modifica la 120 pentru 2 minute)

def load_config():
    """Citește lista de site-uri și produse din JSON."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ Eroare: Fișierul {CONFIG_FILE} nu a fost găsit!")
        return []
    except json.JSONDecodeError:
        print(f"❌ Eroare: Fișierul {CONFIG_FILE} nu este un JSON valid!")
        return []

def main():
    print("🚀 Pornim Monitorul Pokémon TCG...")
    
    # Acest dicționar va ține minte starea fiecărui URL (ex: {"link": "OUT_OF_STOCK"})
    previous_status = {}

    while True:
        try:
            sites = load_config()
            
            if not sites:
                print("⚠️ Nu ai niciun site configurat sau e o eroare în JSON. Pauză 60 secunde...")
                time.sleep(60)
                continue

            print(f"\n[{time.strftime('%H:%M:%S')}] Începem verificarea a {len(sites)} produse...")

            for site in sites:
                result = check_stock_status(site)
                current_status = result["status"]
                url = result["url"]
                name = result["name"]

                print(f"  -> [{name}] Status: {current_status}")

                # Verificăm dacă produsul este acum ÎN STOC
                if current_status == "IN_STOCK":
                    # Dacă înainte NU era în stoc (sau abia am pornit scriptul și dăm de el)
                    if previous_status.get(url) != "IN_STOCK":
                        print(f"🎉 RESTOCK DETECTAT la {name}! Trimit notificare...")
                        
                        # Trimitem pe Telegram
                        notified = send_telegram_notification(name, url)
                        if notified:
                            # Actualizăm starea doar dacă notificarea a fost trimisă cu succes
                            previous_status[url] = "IN_STOCK"
                else:
                    # Dacă e OUT_OF_STOCK sau ERROR, actualizăm starea
                    previous_status[url] = current_status

            print(f"⏳ Verificare completă. Următoarea rulare în {CHECK_INTERVAL // 60} minute...")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n🛑 Script oprit manual de utilizator.")
            break
        except Exception as e:
            print(f"❌ Eroare neașteptată în bucla principală: {e}")
            time.sleep(60)  # Pauză scurtă înainte de a reîncerca pentru a nu face spam

if __name__ == "__main__":
    main()