import os
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path="config/.env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS_RAW = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHAT_IDS = [id.strip() for id in CHAT_IDS_RAW.split(",") if id.strip()]

def _send_raw(chat_id: str, url: str, payload: dict) -> tuple[bool, str]:
    """Trimite un request la Telegram și returnează (success, error_text)."""
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True, ""
        return False, resp.text
    except Exception as e:
        return False, str(e)

def send_telegram_notification(product_name, product_url, product_price, site_name, image_url=None, is_vip=False, vip_message=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        print("⚠️ Configurație Telegram incompletă (Token sau Chat IDs lipsă).")
        return False

    # Header: mesaj VIP custom > generic VIP > normal
    if is_vip and vip_message:
        header = f"💎 <b>{vip_message}</b>"
    elif is_vip:
        header = "💎 <b>SUPER DROP VIP!!!</b> 💎"
    else:
        header = "🚨 <b>PRODUS ÎN STOC!</b> 🚨"

    caption = (
        f"{header}\n\n"
        f"🏛 <b>Magazin:</b> {site_name}\n"
        f"📦 <b>Produs:</b> {product_name}\n"
        f"💰 <b>Preț:</b> {product_price}\n\n"
        f"🔗 <a href='{product_url}'>Vezi pe site</a>"
    )

    success = True
    for chat_id in TELEGRAM_CHAT_IDS:
        sent = False

        # --- Încearcă cu imagine ---
        if image_url:
            photo_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload   = {"chat_id": chat_id, "photo": image_url, "caption": caption, "parse_mode": "HTML"}
            ok, err   = _send_raw(chat_id, photo_url, payload)
            if ok:
                sent = True
            else:
                print(f"⚠️ [{site_name}] Imagine invalidă pentru {chat_id}, trimit text simplu... ({err[:80]})")

        # --- Fallback (sau direct) la mesaj text ---
        if not sent:
            msg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload  = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": False}
            ok, err  = _send_raw(chat_id, msg_url, payload)
            if not ok:
                print(f"❌ Eroare la trimiterea către {chat_id}: {err}")
                success = False

    return success