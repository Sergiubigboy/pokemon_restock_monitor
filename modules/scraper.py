import random
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Listă de User-Agents pentru a evita blocajele de IP
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/114.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15"
]

def check_stock_status(site_config: dict) -> dict:
    """
    Navighează pe site, așteaptă încărcarea, verifică selectorul și determină statusul stocului.
    """
    url = site_config.get("url")
    name = site_config.get("name")
    stock_selector = site_config.get("stock_selector")
    out_of_stock_text = site_config.get("out_of_stock_text", "").lower()

    print(f"🔍 Verificăm stoc pentru: {name}...")

    # ANTI-BOT: Jitter (întârziere aleatorie între 1.5 și 4.5 secunde înainte de fiecare cerere)
    time.sleep(random.uniform(1.5, 4.5))

    with sync_playwright() as p:
        # ANTI-BOT: Alegem un User-Agent la întâmplare
        user_agent = random.choice(USER_AGENTS)

        # Lansăm browser-ul (headless=True înseamnă că rulează invizibil în background)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=user_agent)
        page = context.new_page()

        try:
            # Navigăm către pagină și așteptăm să se încarce DOM-ul (JavaScript-ul)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Așteptăm maxim 10 secunde ca elementul care indică stocul să apară pe pagină
            page.wait_for_selector(stock_selector, timeout=10000)
            
            # Extragem textul de pe site pentru buton/element
            element_text = page.locator(stock_selector).inner_text().strip().lower()

            # Logica de verificare: Dacă textul corespunde cu cel de "stoc epuizat" setat în JSON
            if out_of_stock_text in element_text:
                status = "OUT_OF_STOCK"
            else:
                status = "IN_STOCK"

        except PlaywrightTimeoutError:
            print(f"⚠️ Timeout pentru {name} - Este posibil ca pagina să se fi schimbat sau IP-ul să fie limitat.")
            status = "ERROR"
        except Exception as e:
            print(f"⚠️ Eroare la {name}: {e}")
            status = "ERROR"
        finally:
            browser.close()

    return {"name": name, "status": status, "url": url}