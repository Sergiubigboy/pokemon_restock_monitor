import os
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path="config/.env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_notification(product_name: str, product_url: str, product_price: str, image_url: str = None, is_vip: bool = False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    if is_vip:
        caption = f"💎💎💎 <b>SUPER DROP VIP!!!</b> 💎💎💎\n\n🔥 <b>{product_name}</b>\n💰 Preț: {product_price}\n\n🔗 <a href='{product_url}'>Cumpără INSTANT</a>"
    else:
        caption = f"🚨 <b>PRODUS ÎN STOC!</b> 🚨\n\n📦 {product_name}\n💰 Preț: {product_price}\n\n🔗 <a href='{product_url}'>Vezi pe site</a>"
    
    if image_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "photo": image_url, "caption": caption, "parse_mode": "HTML"}
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": caption, "parse_mode": "HTML"}

    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Eroare Telegram: {e}")
        return False