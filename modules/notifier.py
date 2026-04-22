import os
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path="config/.env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Acum preluăm ID-urile ca un string și îl transformăm în listă
CHAT_IDS_RAW = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHAT_IDS = [id.strip() for id in CHAT_IDS_RAW.split(",") if id.strip()]

def send_telegram_notification(product_name, product_url, product_price, site_name, image_url=None, is_vip=False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        print("⚠️ Configurație Telegram incompletă (Token sau Chat IDs lipsă).")
        return False

    # Construim mesajul incluzând sursa (site-ul)
    header = "💎 <b>SUPER DROP VIP!!!</b> 💎" if is_vip else "🚨 <b>PRODUS ÎN STOC!</b> 🚨"
    caption = (
        f"{header}\n\n"
        f"🏛 <b>Magazin:</b> {site_name}\n"
        f"📦 <b>Produs:</b> {product_name}\n"
        f"💰 <b>Preț:</b> {product_price}\n\n"
        f"🔗 <a href='{product_url}'>Vezi pe site</a>"
    )
    
    success = True
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            if image_url:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                payload = {"chat_id": chat_id, "photo": image_url, "caption": caption, "parse_mode": "HTML"}
            else:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML"}
            
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                print(f"❌ Eroare la trimiterea către {chat_id}: {resp.text}")
                success = False
        except Exception as e:
            print(f"❌ Eroare Telegram pentru ID {chat_id}: {e}")
            success = False
    
    return success