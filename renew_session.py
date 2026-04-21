import json
import os
import time
from playwright.sync_api import sync_playwright

def load_sites():
    """Citește site-urile direct din configurație."""
    try:
        with open("config/sites_config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Eroare la citirea sites_config.json: {e}")
        return []

def renew_profile(site):
    """Deschide browser-ul vizibil pentru a salva amprenta."""
    name = site["name"]
    url = site["url"]
    profile_folder = site.get("profile_folder", "default_profile")
    
    print(f"\n🌐 Deschidem Chrome/Edge pentru: {name}")
    
    # Calea exactă către folderul profilului acestui site
    user_data_dir = os.path.join(os.getcwd(), "config", "profiles", profile_folder)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="msedge", 
            headless=False,  # Întotdeauna VIZIBIL aici ca să poți face bifa
            viewport={"width": 1280, "height": 720},
            args=["--disable-blink-features=AutomationControlled", "--disable-infobars"]
        )

        page = context.pages[0] if context.pages else context.new_page()
        
        print(f"Navigăm spre {url}...")
        page.goto(url)

        print("\n⏳ Ai 40 de secunde să rezolvi bifa (dacă apare) și să dai accept la cookies.")
        print("După ce vezi produsele, poți aștepta pur și simplu să se termine cronometrul.")
        
        # Un cronometru vizual ca să știi cât mai ai
        for i in range(40, 0, -1):
            print(f"Așteptăm... {i} secunde rămase ", end="\r")
            time.sleep(1)

        print(f"\n\n✅ Gata! Profilul pentru '{name}' a fost actualizat cu succes în folderul '{profile_folder}'!")
        context.close()

def main():
    sites = load_sites()
    if not sites:
        print("Niciun site configurat. Verifică config/sites_config.json")
        return

    print("\n=== 🛠️ MANAGER DE SESIUNI (RENEW COOKIES) 🛠️ ===")
    
    # Afișăm o listă dinamică cu toate site-urile din JSON
    for i, site in enumerate(sites):
        print(f"{i + 1}. {site['name']}")
    
    alegere = input("\nIntroduceți numărul site-ului blocat (sau 'q' pentru ieșire): ")
    
    if alegere.lower() == 'q':
        return
        
    try:
        index = int(alegere) - 1
        if 0 <= index < len(sites):
            renew_profile(sites[index])
        else:
            print("❌ Număr invalid!")
    except ValueError:
        print("❌ Te rog să introduci un număr valid.")

if __name__ == "__main__":
    main()