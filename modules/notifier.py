import os
import requests
import time
import threading
from dotenv import load_dotenv

load_dotenv(dotenv_path="config/.env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("TELEGRAM_CHAT_ID_ADMIN", "").strip()
if not ADMIN_ID:
    ADMIN_ID = os.getenv("TELEGRAM_CHAT_ID", "").split(",")[0].strip()

VIP_IDS_RAW = os.getenv("TELEGRAM_CHAT_ID_VIP", "")
VIP_IDS = [id.strip() for id in VIP_IDS_RAW.split(",") if id.strip()]
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

# Expose ALLOWED_CHAT_IDS for bot_controller
ALLOWED_CHAT_IDS = set(VIP_IDS)
if ADMIN_ID:
    ALLOWED_CHAT_IDS.add(ADMIN_ID)

delayed_messages = []
delay_lock = threading.Lock()

def _delayed_message_worker():
    while True:
        time.sleep(1)
        now = time.time()
        with delay_lock:
            ready_messages = [m for m in delayed_messages if m['send_time'] <= now]
            for m in ready_messages:
                delayed_messages.remove(m)
        
        for m in ready_messages:
            ok, err = _send_raw(m['chat_id'], m['url'], m['payload'])
            if not ok and 'photo' in m['payload']:
                # fallback to text
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {"chat_id": m['chat_id'], "text": m['payload']['caption'], "parse_mode": "HTML", "disable_web_page_preview": False}
                _send_raw(m['chat_id'], url, payload)

threading.Thread(target=_delayed_message_worker, daemon=True).start()

def _send_raw(chat_id: str, url: str, payload: dict) -> tuple[bool, str]:
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True, ""
        return False, resp.text
    except Exception as e:
        return False, str(e)

def send_telegram_notification(product_name, product_url, product_price, site_name, image_url=None, is_vip=False, vip_message=None, debug_target=None, delay_seconds=0):
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ Configurație Telegram incompletă (Token lipsă).")
        return False

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

    targets = []
    
    if debug_target == 'admin':
        if ADMIN_ID: targets.append({'chat_id': ADMIN_ID, 'delay': 0})
    elif debug_target == 'all':
        if ADMIN_ID: targets.append({'chat_id': ADMIN_ID, 'delay': 0})
        for v in VIP_IDS: targets.append({'chat_id': v, 'delay': 0})
        if CHANNEL_ID: targets.append({'chat_id': CHANNEL_ID, 'delay': 0})
    else:
        if ADMIN_ID: targets.append({'chat_id': ADMIN_ID, 'delay': 0})
        for v in VIP_IDS: targets.append({'chat_id': v, 'delay': 0})
        if CHANNEL_ID: targets.append({'chat_id': CHANNEL_ID, 'delay': delay_seconds})

    success = True
    now = time.time()
    
    for t in targets:
        chat_id = t['chat_id']
        delay = t['delay']
        
        if image_url:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {"chat_id": chat_id, "photo": image_url, "caption": caption, "parse_mode": "HTML"}
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": False}
        
        if delay > 0:
            with delay_lock:
                delayed_messages.append({'chat_id': chat_id, 'url': url, 'payload': payload, 'send_time': now + delay})
        else:
            ok, err = _send_raw(chat_id, url, payload)
            if not ok and image_url:
                # fallback
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": False}
                ok, err = _send_raw(chat_id, url, payload)
            if not ok:
                print(f"❌ Eroare la trimiterea către {chat_id}: {err}")
                success = False

    return success