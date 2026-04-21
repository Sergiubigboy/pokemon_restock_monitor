import os
import time
from playwright.sync_api import sync_playwright

def get_full_html(url, name):
    print("🌐 Deschidem browser-ul pentru a extrage HTML-ul...")
    user_data_dir = os.path.join(os.getcwd(), "config", "chrome_profile")

    with sync_playwright() as p:
        # ASIGURĂ-TE că pui aici "msedge" sau "chrome", exact cum a mers la setup_session.py
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="msedge", 
            headless=False, # Îl lăsăm vizibil ca să vezi ce face
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        page = context.pages[0] if context.pages else context.new_page()
        
        print("Mergem pe site...")
        page.goto(url)
        
        print("⏳ Așteptăm 10 secunde să se încarce absolut tot (inclusiv produsele ascunse)...")
        time.sleep(10) 
        
        # Extragem tot HTML-ul exact cum arată în secunda asta
        html_content = page.content()
        
        # Îl salvăm într-un fișier text
        with open(f"{name}_source.txt", "w", encoding="utf-8") as f:
            f.write(html_content)
            
        print(f"✅ Gata! Am salvat tot HTML-ul în fișierul '{name}_source.txt'.")
        context.close()

if __name__ == "__main__":
    input_url = input("Introdu URL-ul paginii de căutare: ")
    input_name = input("Dă un nume scurt pentru acest site (fără spații, ex: 'emag'): ")
    get_full_html(input_url, input_name)