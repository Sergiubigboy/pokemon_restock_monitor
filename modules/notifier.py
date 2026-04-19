import os
import requests
from dotenv import load_dotenv

# Încărcăm variabilele de mediu din fișierul .env aflat în folderul config
load_dotenv(dotenv_path="config/.env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_notification(product_name: str, product_url: str):
    """
    Trimite un mesaj pe Telegram când un produs revine în stoc.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Eroare: TELEGRAM_BOT_TOKEN sau TELEGRAM_CHAT_ID lipsesc din .env!")
        return False

    message = f"🚨 <b>RESTOCK POKÉMON!</b> 🚨\n\n📦 <b>Produs:</b> {product_name}\n🔗 <b>Link:</b> <a href='{product_url}'>Cumpără Acum</a>"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print(f"✅ Notificare trimisă cu succes pentru: {product_name}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Eroare la trimiterea notificării pe Telegram: {e}")
        return False