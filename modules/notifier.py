import html
import os
import requests
import time
import threading
from dotenv import load_dotenv

from modules.price_parser import format_ron, parse_price_ron
from modules.feedback import token_pentru
from modules import beta

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
                url = _api("sendMessage")
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

def _token() -> str:
    """Tokenul de folosit ACUM. In beta, cel de test; altfel cel normal."""
    return beta.token_beta() if beta.e_activ() else TELEGRAM_BOT_TOKEN


def _api(metoda: str) -> str:
    return f"https://api.telegram.org/bot{_token()}/{metoda}"


def _tinte(debug_target=None, delay_seconds=0) -> list:
    """
    Cui trimitem. In modul beta, implicit DOAR adminului — canalul public si
    VIP-urile nu au ce cauta intr-un test.
    """
    if beta.e_activ() and beta.doar_admin():
        return [{"chat_id": ADMIN_ID, "delay": 0}] if ADMIN_ID else []

    tinte = []
    if debug_target == "admin":
        if ADMIN_ID:
            tinte.append({"chat_id": ADMIN_ID, "delay": 0})
        return tinte

    if ADMIN_ID:
        tinte.append({"chat_id": ADMIN_ID, "delay": 0})
    for v in VIP_IDS:
        tinte.append({"chat_id": v, "delay": 0})
    if CHANNEL_ID:
        tinte.append({"chat_id": CHANNEL_ID,
                      "delay": 0 if debug_target == "all" else delay_seconds})
    return tinte


def _tastatura_verdict(id_canonic: str):
    """
    Butoanele Good / Bad de sub notificare.

    Telegram limiteaza callback_data la 64 de octeti, deci trimitem un token
    scurt, nu id-ul canonic intreg. Fara id canonic (produs neclasificat) nu
    afisam butoane — un verdict pe care nu-l putem lega de nimic e inutil.
    """
    if not id_canonic:
        return None
    token = token_pentru(id_canonic)
    if not token:
        return None
    return {
        "inline_keyboard": [[
            {"text": "✅ Good", "callback_data": "v:g:" + token},
            {"text": "⛔ Bad (nu mai trimite)", "callback_data": "v:b:" + token},
        ]]
    }


def send_telegram_notification(product_name, product_url, product_price, site_name, image_url=None, is_vip=False, vip_message=None, debug_target=None, delay_seconds=0, id_canonic=None):
    if not _token():
        print("⚠️ Configurație Telegram incompletă (Token lipsă).")
        return False

    if is_vip and vip_message:
        header = f"💎 <b>{vip_message}</b>"
    elif is_vip:
        header = "💎 <b>SUPER DROP VIP!!!</b> 💎"
    else:
        header = "🚨 <b>PRODUS ÎN STOC!</b> 🚨"

    # Unele teme (Redgoblin) scot pretul ca text brut ilizibil:
    #   "Original price 169,00 lei - Original price 169,00 lei ... | /"
    # Afisam valoarea parsata cand o putem citi, altfel textul original.
    pret_numeric = parse_price_ron(product_price)
    pret_afisat = f"{format_ron(pret_numeric)} lei" if pret_numeric else product_price

    caption = (
        f"{beta.eticheta()}{header}\n\n"
        f"🏛 <b>Magazin:</b> {site_name}\n"
        f"📦 <b>Produs:</b> {product_name}\n"
        f"💰 <b>Preț:</b> {pret_afisat}\n\n"
        f"🔗 <a href='{product_url}'>Vezi pe site</a>"
    )

    tastatura = _tastatura_verdict(id_canonic)
    targets = _tinte(debug_target, delay_seconds)
    success = True
    now = time.time()
    
    for t in targets:
        chat_id = t['chat_id']
        delay = t['delay']
        
        if image_url:
            url = _api("sendPhoto")
            payload = {"chat_id": chat_id, "photo": image_url, "caption": caption, "parse_mode": "HTML"}
            if tastatura and chat_id == ADMIN_ID:
                payload["reply_markup"] = tastatura
        else:
            url = _api("sendMessage")
            payload = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": False}
            if tastatura and chat_id == ADMIN_ID:
                payload["reply_markup"] = tastatura

        if delay > 0:
            with delay_lock:
                delayed_messages.append({'chat_id': chat_id, 'url': url, 'payload': payload, 'send_time': now + delay})
        else:
            ok, err = _send_raw(chat_id, url, payload)
            if not ok and image_url:
                # fallback
                url = _api("sendMessage")
                payload = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": False}
                ok, err = _send_raw(chat_id, url, payload)
            if not ok:
                print(f"❌ Eroare la trimiterea către {chat_id}: {err}")
                success = False

    return success


# ─────────────────────────────────────────────────────────────────
#  ALERTE WATCHLIST (oportunitate de cumpărare cu profit calculat)
#
#  Funcții noi, separate de send_telegram_notification de mai sus —
#  fluxul vechi rămâne neatins.
# ─────────────────────────────────────────────────────────────────

# Traducerea nivelului de încredere pus de agentul săptămânal.
_INCREDERE = {"high": "mare", "medium": "medie", "low": "mică"}


def _nume_scurt_magazin(site_name: str) -> str:
    """"Pokemon TCG - Krit" -> "Krit". Numele lung nu aduce nimic în alertă."""
    if " - " in site_name:
        return site_name.rsplit(" - ", 1)[-1].strip()
    return site_name.strip()


def _vechime_text(zile) -> str:
    if zile is None:
        return "preț neverificat"
    if zile <= 0:
        return "preț verificat azi"
    if zile == 1:
        return "preț verificat ieri"
    return f"preț verificat acum {zile} zile"


def build_watchlist_message(decision, product_name: str, product_url: str,
                            site_name: str, stock_qty=None) -> str:
    """
    Compune textul alertei de oportunitate (HTML pentru Telegram).

    E separată de trimitere ca simulatorul local să poată afișa mesajul exact
    fără să atingă Telegram.
    """
    magazin = _nume_scurt_magazin(site_name)
    stoc = f" · {stock_qty} buc în stoc" if stock_qty else ""

    net = decision.net_profit_ron or 0.0
    roi = (decision.roi_pct or 0.0) * 100
    total = decision.total_profit_ron or 0.0

    # Coloanele se aliniază doar cu font monospaced, deci blocul e <pre>.
    tabel = "\n".join([
        f"{'Achiziție':<14}{format_ron(decision.price_ron)} RON  (plafon {format_ron(decision.max_price_ron)})",
        f"{'Revânzare':<14}{format_ron(decision.resale_ron)} RON  ({decision.resale_source})",
        f"{'Profit net':<14}{net:+.0f} RON  ·  {roi:+.0f}% ROI",
        f"{'Lichiditate':<14}{decision.liquidity_30d} vânzări / 30 zile",
    ])

    incredere = _INCREDERE.get(decision.confidence, decision.confidence or "necunoscută")

    return (
        f"{beta.eticheta()}🚨 <b>OPORTUNITATE LIVE</b> · {html.escape(decision.niche)}\n\n"
        f"<b>{html.escape(decision.label or product_name)}</b>\n"
        f"{html.escape(magazin)}{html.escape(stoc)}\n\n"
        f"<pre>{html.escape(tabel)}</pre>\n"
        f"<i>{_vechime_text(decision.resale_age_days)} · încredere {incredere}</i>\n\n"
        f"Max recomandat: <b>{decision.max_qty} buc → {total:+.0f} RON</b>\n\n"
        f"📦 {html.escape(product_name)}\n"
        f"🔗 <a href='{html.escape(product_url or '', quote=True)}'>Cumpără pe {html.escape(magazin)}</a>"
    )


def send_watchlist_alert(decision, product_name: str, product_url: str, site_name: str,
                         image_url=None, stock_qty=None, debug_target=None, delay_seconds=0, id_canonic=None):
    """
    Trimite alerta de oportunitate. Aceleași ținte și aceeași logică de delay
    ca notificarea clasică, dar cu mesajul de profit.
    """
    if not _token():
        print("⚠️ Configurație Telegram incompletă (Token lipsă).")
        return False

    caption = build_watchlist_message(decision, product_name, product_url, site_name, stock_qty)

    tastatura = _tastatura_verdict(id_canonic)
    targets = _tinte(debug_target, delay_seconds)
    success = True
    now = time.time()

    for t in targets:
        chat_id = t['chat_id']
        delay   = t['delay']

        if image_url:
            url = _api("sendPhoto")
            payload = {"chat_id": chat_id, "photo": image_url, "caption": caption, "parse_mode": "HTML"}
            if tastatura and chat_id == ADMIN_ID:
                payload["reply_markup"] = tastatura
        else:
            url = _api("sendMessage")
            payload = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": False}
            if tastatura and chat_id == ADMIN_ID:
                payload["reply_markup"] = tastatura

        if delay > 0:
            with delay_lock:
                delayed_messages.append({'chat_id': chat_id, 'url': url, 'payload': payload, 'send_time': now + delay})
        else:
            ok, err = _send_raw(chat_id, url, payload)
            if not ok and image_url:
                # fallback pe text dacă poza nu trece (link expirat, hotlink blocat)
                url = _api("sendMessage")
                payload = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": False}
                ok, err = _send_raw(chat_id, url, payload)
            if not ok:
                print(f"❌ Eroare la trimiterea alertei watchlist către {chat_id}: {err}")
                success = False

    return success

# ─────────────────────────────────────────────────────────────────
#  ALERTA DE SET — decizia e deja luata, nu se calculeaza acum
# ─────────────────────────────────────────────────────────────────
def build_set_message(verdict, product_name: str, product_url: str,
                      site_name: str, product_price: str, stock_qty=None) -> str:
    """
    Mesajul pentru un produs dintr-un set cercetat (tier S/A).

    Nu contine cifre de evaluat. Un produs bun intra o singura data si nu e
    timp de gandit — tot ce trebuia gandit s-a gandit la research, cu
    saptamani inainte. Aici doar se citeste verdictul.
    """
    magazin = _nume_scurt_magazin(site_name)
    pret = parse_price_ron(product_price)
    pret_afisat = f"{format_ron(pret)} lei" if pret else (product_price or "?")
    stoc = f" · {stock_qty} buc" if stock_qty else ""

    if verdict.tier_set == "S":
        antet = "🔥 <b>CUMPARA ACUM</b>"
    else:
        antet = "⚡ <b>MERITA</b>"

    linii = [
        f"{beta.eticheta()}{antet} · {html.escape(verdict.nisa)}",
        "",
        f"<b>{html.escape(product_name)}</b>",
        f"{html.escape(magazin)} · {pret_afisat}{stoc}",
        "",
    ]

    if verdict.explicatie_set:
        eticheta = "Decis la research"
        if verdict.categorie_set == "lansare_viitoare" and verdict.lanseaza_la:
            eticheta = f"Lansare {verdict.lanseaza_la}"
        linii.append(f"<i>{eticheta}: {html.escape(verdict.explicatie_set)}</i>")
        linii.append("")

    linii.append(f"🔗 <a href='{html.escape(product_url or '', quote=True)}'>"
                 f"Cumpara pe {html.escape(magazin)}</a>")

    if verdict.surse:
        prima = verdict.surse[0]
        if str(prima).startswith("http"):
            linii.append(f"📎 <a href='{html.escape(prima, quote=True)}'>sursa</a>")

    return chr(10).join(linii)


def send_set_alert(verdict, product_name: str, product_url: str, site_name: str,
                   product_price: str, image_url=None, stock_qty=None,
                   debug_target=None, delay_seconds=0, id_canonic=None):
    """Trimite alerta de set. Aceleasi tinte ca notificarea clasica."""
    if not _token():
        return False

    caption = build_set_message(verdict, product_name, product_url, site_name,
                                product_price, stock_qty)
    tastatura = _tastatura_verdict(id_canonic)

    targets = _tinte(debug_target, delay_seconds)
    success = True
    now = time.time()
    for t in targets:
        chat_id, delay = t['chat_id'], t['delay']
        if image_url:
            url = _api("sendPhoto")
            payload = {"chat_id": chat_id, "photo": image_url, "caption": caption,
                       "parse_mode": "HTML"}
        else:
            url = _api("sendMessage")
            payload = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML",
                       "disable_web_page_preview": False}
        if tastatura and chat_id == ADMIN_ID:
            payload["reply_markup"] = tastatura

        if delay > 0:
            with delay_lock:
                delayed_messages.append({'chat_id': chat_id, 'url': url,
                                         'payload': payload, 'send_time': now + delay})
        else:
            ok, err = _send_raw(chat_id, url, payload)
            if not ok and image_url:
                url = _api("sendMessage")
                payload = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML",
                           "disable_web_page_preview": False}
                ok, err = _send_raw(chat_id, url, payload)
            if not ok:
                print(f"❌ Eroare la alerta de set catre {chat_id}: {err}")
                success = False
    return success
