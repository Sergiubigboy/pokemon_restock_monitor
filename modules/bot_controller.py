import json
import os
import time
import threading
import requests
from dotenv import load_dotenv
from modules.state_manager import load_muted_sites, save_muted_sites
from modules.watchlist import alerts_today, load_watchlist, watchlist_is_stale
from modules.item_stats import raport as item_stats_raport
from modules import beta, classifier, feedback, policy, price_check

load_dotenv(dotenv_path="config/.env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("TELEGRAM_CHAT_ID_ADMIN", "").strip()
if not ADMIN_ID:
    # Fallback to old var just in case
    ADMIN_ID = os.getenv("TELEGRAM_CHAT_ID", "").split(",")[0].strip()

VIP_IDS_RAW = os.getenv("TELEGRAM_CHAT_ID_VIP", "")
VIP_IDS = [id.strip() for id in VIP_IDS_RAW.split(",") if id.strip()]
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

ALLOWED_CHAT_IDS = set(VIP_IDS)
if ADMIN_ID:
    ALLOWED_CHAT_IDS.add(ADMIN_ID)
# ─────────────────────────────────────────────────────────────────
#  Stare partajată cu main loop — toate operațiile sunt thread-safe
# ─────────────────────────────────────────────────────────────────
class MonitorState:
    def __init__(self):
        self._lock          = threading.Lock()
        self.turbo_mode     = False
        self.paused         = False
        self.debug_mode     = False
        self.debug_mode_all = False
        self.delay_mode     = False
        self.delay_seconds  = 40
        # Secunde intre cicluri. Cu 20+ magazine si mai multe categorii pe
        # acelasi domeniu, 3s inseamna ca lovesti un magazin de cateva ori pe
        # secunda si te taie. 15s e ritmul sanatos la scara asta.
        self.check_interval = 15        # modificabil via /interval
        # Evaluarea pe watchlist — pornită/oprită la cald cu /watchlist on|off.
        # E aditivă: când e ON, produsele de pe watchlist primesc în plus alerta
        # cu profit și ROI. Nimic din fluxul vechi nu dispare.
        self.watchlist_enabled = True
        # Câte nișe se scanează simultan. În interiorul unei nișe magazinele
        # rămân STRICT pe rând — paralelismul e doar între nișe.
        # 1 = comportamentul secvențial de dinainte. Crește-l doar dacă
        # `free -m` pe Pi arată că mai ai RAM liber.
        self.parallel_niches = 2
        # Clasificarea produselor. ON implicit — stratul local nu costa
        # nimic si nu are nevoie de retea. Gemini intervine doar pentru
        # numele pe care regulile locale nu le pot citi.
        self.classifier_enabled = True
        self.last_scan      = "Niciodată"
        self.scan_count     = 0
        self.start_time     = time.time()
        self.errors_log     = []           # ultimele 10 erori
        self.site_stats     = {}           # site_name -> {"ok": N, "fail": N, "products": N}
        self.muted_sites    = load_muted_sites()

    # ── Mod / Pauză ────────────────────────────────────────────
    def set_turbo(self, val: bool):
        with self._lock:
            self.turbo_mode = val

    def set_paused(self, val: bool):
        with self._lock:
            self.paused = val

    def toggle_debug(self) -> bool:
        with self._lock:
            self.debug_mode = not self.debug_mode
            return self.debug_mode

    def set_interval(self, seconds: int):
        with self._lock:
            self.check_interval = seconds

    def get_interval(self) -> int:
        with self._lock:
            return self.check_interval

    def toggle_debug_all(self) -> bool:
        with self._lock:
            self.debug_mode_all = not self.debug_mode_all
            return self.debug_mode_all
            
    def toggle_delay_mode(self) -> bool:
        with self._lock:
            self.delay_mode = not self.delay_mode
            return self.delay_mode
            
    def set_delay_seconds(self, sec: int):
        with self._lock:
            self.delay_seconds = sec

    # ── Watchlist ──────────────────────────────────────────────
    def set_watchlist(self, val: bool):
        with self._lock:
            self.watchlist_enabled = val

    def is_watchlist_enabled(self) -> bool:
        with self._lock:
            return self.watchlist_enabled

    # ── Paralelism pe nișe ─────────────────────────────────────
    def set_parallel_niches(self, n: int):
        with self._lock:
            self.parallel_niches = max(1, min(n, 4))

    def get_parallel_niches(self) -> int:
        with self._lock:
            return self.parallel_niches

    # ── Clasificator LLM ───────────────────────────────────────
    def set_classifier(self, val: bool):
        with self._lock:
            self.classifier_enabled = val

    def is_classifier_enabled(self) -> bool:
        with self._lock:
            return self.classifier_enabled

    # ── Statistici scanare ─────────────────────────────────────
    def record_scan(self):
        with self._lock:
            self.scan_count += 1
            self.last_scan = time.strftime("%H:%M:%S")

    def record_site_ok(self, site_name: str, product_count: int):
        with self._lock:
            s = self.site_stats.setdefault(site_name, {"ok": 0, "fail": 0, "products": 0, "consec_fail": 0})
            s["ok"]          += 1
            s["products"]     = product_count
            s["consec_fail"]  = 0

    def get_site_product_count(self, site_name: str) -> int:
        """Câte produse a întors magazinul la ultima scanare reușită."""
        with self._lock:
            return self.site_stats.get(site_name, {}).get("products", 0)

    def record_site_fail(self, site_name: str) -> int:
        """Returnează numărul de eșecuri consecutive după înregistrare."""
        with self._lock:
            s = self.site_stats.setdefault(site_name, {"ok": 0, "fail": 0, "products": 0, "consec_fail": 0})
            s["fail"]        += 1
            s["consec_fail"] += 1
            return s["consec_fail"]

    # ── Erori ─────────────────────────────────────────────────
    def record_error(self, msg: str, site: str = None):
        with self._lock:
            ts    = time.strftime("%H:%M:%S")
            entry = f"[{ts}]" + (f" [{site}]" if site else "") + f" {msg}"
            self.errors_log.append(entry)
            if len(self.errors_log) > 20:
                self.errors_log.pop(0)

    def clear_errors(self):
        with self._lock:
            self.errors_log.clear()

    # ── Mute / Unmute ─────────────────────────────────────────
    def mute_site(self, site_name: str):
        with self._lock:
            self.muted_sites.add(site_name.lower())
            save_muted_sites(self.muted_sites)

    def unmute_site(self, site_name: str):
        with self._lock:
            self.muted_sites.discard(site_name.lower())
            save_muted_sites(self.muted_sites)

    def is_muted(self, site_name: str) -> bool:
        with self._lock:
            return site_name.lower() in self.muted_sites

    def get_muted_copy(self) -> set:
        with self._lock:
            return set(self.muted_sites)

    # ── Text-uri pentru comenzi ───────────────────────────────
    def get_status_text(self) -> str:
        with self._lock:
            turbo   = self.turbo_mode
            paused  = self.paused
            debug   = self.debug_mode
            iv      = self.check_interval
            uptime  = int(time.time() - self.start_time)
            h, rem  = divmod(uptime, 3600)
            m, s    = divmod(rem, 60)
            scans   = self.scan_count
            last    = self.last_scan
            muted   = len(self.muted_sites)
            errors  = len(self.errors_log)
            wl_on   = self.watchlist_enabled
            paralel = self.parallel_niches
            clf_on  = self.classifier_enabled
        from modules import beta as _beta
        beta_on = _beta.e_activ()

        mode_str    = "⚡ TURBO (1s)" if turbo else f"🐢 Normal ({iv}s)"
        state_str   = "⏸ PAUZĂ" if paused else "▶️ Activ"
        debug_str   = "🔍 ON (ADMIN)" if debug else ("🔍 ON (ALL)" if self.debug_mode_all else "OFF")
        delay_str   = f"⏱ ON ({self.delay_seconds}s)" if self.delay_mode else "OFF"

        lines = [
            "📊 <b>Status Monitor</b>\n",
            f"📡 <b>Stare:</b>    {state_str}",
            f"🔄 <b>Mod:</b>      {mode_str}",
            f"🔍 <b>Debug:</b>    {debug_str}",
            f"⏳ <b>Delay Ch:</b>  {delay_str}",
            f"🎯 <b>Watchlist:</b> {'✅ ON' if wl_on else '⛔ OFF'}",
            f"🧵 <b>Nișe paralel:</b> {paralel}",
            f"🧠 <b>Clasificator:</b> {'✅ ON' if clf_on else '⛔ OFF'}",
            f"🧪 <b>Mod BETA:</b> {'✅ ACTIV' if beta_on else 'oprit'}"
            f"🕐 <b>Uptime:</b>   {h:02d}:{m:02d}:{s:02d}",
            f"🔢 <b>Scanări:</b>  {scans}",
            f"⏱ <b>Ultima:</b>   {last}",
            f"🔇 <b>Muted:</b>    {muted} site(uri)",
            f"❌ <b>Erori log:</b> {errors} (vezi /errors)",
        ]
        return "\n".join(lines)

    def get_errors_text(self) -> str:
        with self._lock:
            log = list(self.errors_log)

        if not log:
            return "✅ <b>Nu există erori înregistrate.</b>"

        lines = [f"❌ <b>Ultimele {len(log)} erori:</b>\n"]
        for e in reversed(log):
            lines.append(f"• <code>{e}</code>")
        return "\n".join(lines)

    def get_stats_text(self) -> str:
        with self._lock:
            stats = dict(self.site_stats)
            muted = set(self.muted_sites)

        if not stats:
            return "📈 Nu există statistici încă. Primul scan nu s-a terminat."

        lines = ["📈 <b>Statistici per site:</b>\n"]
        for name, s in stats.items():
            ok   = s["ok"]
            fail = s["fail"]
            prod = s["products"]
            cf   = s["consec_fail"]
            mute_icon = "🔇" if name.lower() in muted else ""
            fail_icon = f" ⚠️ {cf} eșec(uri) consecutive" if cf >= 2 else ""
            lines.append(
                f"{mute_icon}{'🔴' if fail > ok else '🟢'} <b>{name}</b>\n"
                f"   ✅ {ok} ok  ❌ {fail} fail  📦 {prod} produse{fail_icon}"
            )
        return "\n\n".join(lines)


# ─────────────────────────────────────────────────────────────────
#  Singleton stare globală
# ─────────────────────────────────────────────────────────────────
monitor_state = MonitorState()

# ─────────────────────────────────────────────────────────────────
#  Funcții Telegram low-level
# ─────────────────────────────────────────────────────────────────
def _token() -> str:
    """In modul beta, comenzile merg pe botul de test, nu pe cel real."""
    return beta.token_beta() if beta.e_activ() else TELEGRAM_BOT_TOKEN


def _send_message(chat_id: str, text: str):
    if not _token():
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_token()}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10
        )
    except Exception as e:
        print(f"⚠️ [Bot] Eroare la trimitere mesaj: {e}")

def _broadcast(text: str):
    """Trimite un mesaj tuturor chat ID-urilor autorizate."""
    for cid in ALLOWED_CHAT_IDS:
        _send_message(cid, text)

def _get_updates(offset: int) -> list:
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{_token()}/getUpdates",
            params={"offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]},
            timeout=40
        )
        if resp.status_code == 200:
            return resp.json().get("result", [])
    except Exception:
        pass
    return []

# ─────────────────────────────────────────────────────────────────
#  Helper — config sites
# ─────────────────────────────────────────────────────────────────
def _load_sites_config() -> list:
    try:
        with open("config/sites_config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _find_site_match(partial: str, sites: list) -> list:
    return [s for s in sites if partial.lower() in s["name"].lower()]

# ─────────────────────────────────────────────────────────────────
#  HELP text — NOTE: use &lt; &gt; pentru < > în HTML mode
# ─────────────────────────────────────────────────────────────────
HELP_TEXT = (
    "🤖 <b>Comenzi disponibile:</b>\n\n"
    "<b>── Control ──</b>\n"
    "⚡ /turbo — Turbo Mode (interval 1s)\n"
    "🐢 /normal — Mod normal (5 min)\n"
    "⏸ /pause — Pauză scanare (Admin)\n"
    "▶️ /resume — Reia scanarea (Admin)\n"
    "🔍 /debug — Toggle debug admin (Admin)\n"
    "🔍 /debugall — Toggle debug toți (Admin)\n"
    "⏱ /interval &lt;sec&gt; — Seteaza intervalul (Admin)\n"
    "⏱ /delay — Toggle delay pentru canal (Admin)\n"
    "⏱ /setdelay &lt;sec&gt; — Setare delay (Admin)\n"
    "📢 /say &lt;msg&gt; — Trimite mesaj pe canal (Admin)\n\n"
    "<b>── Watchlist ──</b>\n"
    "🎯 /watchlist — Starea evaluării pe watchlist\n"
    "🎯 /watchlist on|off — Pornește/oprește evaluarea (Admin)\n"
    "📉 /performance — Ce item-uri merită slotul\n"
    "🧵 /parallel &lt;1-4&gt; — Câte nișe scanez simultan (Admin)\n"
    "🧠 /classifier on|off — Filtrare inteligentă cu AI (Admin)\n"
    "🎯 /nise — Politica si seturile pe fiecare nisa\n"
    "📊 /preturi — Registrul de preturi second-hand\n"
    "🧪 /beta on|off — Ruleaza versiunea de test (Admin)\n"
    "🗳 /feedback — Verdictele tale Good/Bad\n"
    "🔓 /unblock &lt;id&gt; — Deblochează un produs (Admin)\n\n"
    "<b>── Info ──</b>\n"
    "📊 /status — Starea curentă\n"
    "❌ /errors — Ultimele erori\n"
    "🗑 /clearerrors — Șterge log-ul de erori\n"
    "📈 /stats — Statistici per site\n\n"
    "<b>── Site-uri ──</b>\n"
    "🌐 /sites — Lista site-urilor\n"
    "🔇 /mute &lt;site&gt; — Dă mute unui site\n"
    "🔔 /unmute &lt;site&gt; — Reactivează un site\n\n"
    "❓ /help — Această listă"
)

# ─────────────────────────────────────────────────────────────────
#  Handler comenzi
# ─────────────────────────────────────────────────────────────────
def _handle_command(chat_id: str, text: str):
    parts = text.strip().split(None, 1)
    cmd   = parts[0].lower().split("@")[0]
    arg   = parts[1].strip() if len(parts) > 1 else ""

    is_admin = (chat_id == ADMIN_ID)
    
    # VIP commands allowed
    vip_cmds = {"/turbo", "/normal", "/status", "/stats", "/help", "/start",
                "/watchlist", "/performance", "/feedback", "/classifier", "/nise", "/preturi", "/beta"}
    
    if not is_admin and cmd not in vip_cmds:
        _send_message(chat_id, "🔒 Nu ai permisiunea pentru această comandă.")
        return

    # ── Control ───────────────────────────────────────────────
    if cmd == "/turbo":
        monitor_state.set_turbo(True)
        _send_message(chat_id,
            "⚡ <b>TURBO MODE ACTIVAT!</b>\n"
            "Scanez la fiecare 1 secundă. Let's go! 🚀")

    elif cmd == "/normal":
        monitor_state.set_turbo(False)
        iv = monitor_state.get_interval()
        _send_message(chat_id,
            f"🐢 <b>Mod normal activat.</b>\n"
            f"Interval: {iv}s ({iv//60} min {iv%60}s).")

    elif cmd == "/pause":
        if monitor_state.paused:
            _send_message(chat_id, "ℹ️ Monitorul e deja pe pauză.")
        else:
            monitor_state.set_paused(True)
            _send_message(chat_id,
                "⏸ <b>Monitor pus pe PAUZĂ.</b>\n"
                "Trimite /resume ca să reiei scanarea.")

    elif cmd == "/resume":
        if not monitor_state.paused:
            _send_message(chat_id, "ℹ️ Monitorul rulează deja.")
        else:
            monitor_state.set_paused(False)
            _send_message(chat_id, "▶️ <b>Monitor RELUAT!</b> Scanez din nou.")

    elif cmd == "/debug":
        active = monitor_state.toggle_debug()
        if active:
            _send_message(chat_id,
                "🔍 <b>DEBUG MODE ON</b>\n"
                "Toate produsele valide vor fi retrimise către Admin.")
        else:
            _send_message(chat_id, "🔍 <b>Debug MODE OFF.</b>")

    elif cmd == "/debugall":
        active = monitor_state.toggle_debug_all()
        if active:
            _send_message(chat_id, "🔍 <b>DEBUG ALL ON</b>\nToate produsele se trimit către toți.")
        else:
            _send_message(chat_id, "🔍 <b>Debug ALL OFF.</b>")

    elif cmd == "/delay":
        active = monitor_state.toggle_delay_mode()
        state = "ACTIVAT" if active else "DEZACTIVAT"
        _send_message(chat_id, f"⏱ <b>Mod Delay {state}</b> pentru Canal.")

    elif cmd == "/setdelay":
        if not arg or not arg.isdigit():
            _send_message(chat_id, "⚠️ Sintaxă: /setdelay <secunde>")
            return
        secs = int(arg)
        monitor_state.set_delay_seconds(secs)
        _send_message(chat_id, f"⏱ <b>Delay setat la {secs} secunde.</b>")

    elif cmd == "/say":
        if not arg:
            _send_message(chat_id, "⚠️ Sintaxă: /say <mesaj>")
            return
        if CHANNEL_ID:
            _send_message(CHANNEL_ID, arg)
            _send_message(chat_id, "✅ Mesaj trimis pe canal.")
        else:
            _send_message(chat_id, "⚠️ Canalul nu e configurat.")

    elif cmd == "/interval":
        if not arg or not arg.isdigit():
            _send_message(chat_id,
                "⚠️ Sintaxă: <code>/interval &lt;secunde&gt;</code>\n"
                "Exemplu: <code>/interval 120</code> pentru 2 minute.")
            return
        secs = int(arg)
        if secs < 5:
            _send_message(chat_id, "⚠️ Intervalul minim e 5 secunde.")
            return
        monitor_state.set_interval(secs)
        _send_message(chat_id,
            f"⏱ <b>Interval setat la {secs}s</b> ({secs//60}m {secs%60}s).\n"
            f"Se aplică din ciclul următor (în mod normal, nu turbo).")

    # ── Watchlist ─────────────────────────────────────────────
    elif cmd == "/watchlist":
        optiune = arg.lower().strip()

        if optiune in ("on", "off"):
            if not is_admin:
                _send_message(chat_id, "🔒 Doar Adminul poate porni/opri watchlist-ul.")
                return
            monitor_state.set_watchlist(optiune == "on")
            if optiune == "on":
                _send_message(chat_id,
                    "🎯 <b>Watchlist ACTIVAT.</b>\n"
                    "Produsele de pe watchlist primesc în plus alerta cu profit net și ROI.\n"
                    "Notificările obișnuite continuă neschimbate.")
            else:
                _send_message(chat_id,
                    "⛔ <b>Watchlist DEZACTIVAT.</b>\n"
                    "Rămâne doar fluxul clasic (VIP + blacklist).")
            return

        if optiune:
            _send_message(chat_id, "⚠️ Sintaxă: <code>/watchlist on</code> sau <code>/watchlist off</code>")
            return

        # Fără argument: raport de stare.
        wl = load_watchlist()
        activ = "✅ ON" if monitor_state.is_watchlist_enabled() else "⛔ OFF"

        if not wl:
            _send_message(chat_id,
                f"🎯 <b>Watchlist:</b> {activ}\n\n"
                "⚠️ <b>Fișierul nu s-a putut încărca</b> (lipsă sau JSON invalid).\n"
                "Monitorul rulează pe fluxul clasic.")
            return

        items = [i for i in wl.get("items", []) if i.get("enabled")]
        lines = [
            f"🎯 <b>Watchlist:</b> {activ}",
            f"📋 <b>Item-uri active:</b> {len(items)} din {len(wl.get('items', []))}",
        ]

        eticheta = (wl.get("_meta") or {}).get("week_label")
        if eticheta:
            lines.append(f"🗓 <b>Săptămâna:</b> {eticheta}")
        if watchlist_is_stale(wl):
            lines.append("\n⚠️ <b>Watchlist EXPIRAT</b> — agentul săptămânal nu a mai rulat.\nPrețurile de revânzare nu mai sunt de încredere.")

        lines.append("\n<b>Alerte trimise azi:</b>")
        for i in sorted(items, key=lambda x: str(x.get("tier", ""))):
            trimise = alerts_today(str(i.get("id", "")))
            lines.append(f"  [{i.get('tier', '?')}] {i.get('label', i.get('id'))} — {trimise}")

        _send_message(chat_id, "\n".join(lines))

    elif cmd == "/performance":
        _send_message(chat_id, item_stats_raport(load_watchlist()))

    elif cmd == "/parallel":
        if not arg or not arg.isdigit():
            actual = monitor_state.get_parallel_niches()
            _send_message(chat_id,
                f"🧵 <b>Nișe scanate simultan:</b> {actual}\n\n"
                "Sintaxă: <code>/parallel 2</code> (între 1 și 4).\n"
                "Magazinele dintr-o nișă rămân mereu pe rând — "
                "paralelismul e doar între nișe.")
            return
        n = int(arg)
        monitor_state.set_parallel_niches(n)
        efectiv = monitor_state.get_parallel_niches()
        avertisment = ""
        if efectiv >= 3:
            avertisment = ("\n\n⚠️ Peste 2 nișe simultan pot rula mai multe instanțe "
                           "Edge deodată. Verifică <code>free -m</code> pe Pi.")
        _send_message(chat_id,
            f"🧵 <b>Scanez {efectiv} nișe simultan.</b>\n"
            f"Se aplică din ciclul următor.{avertisment}")

    elif cmd == "/classifier":
        optiune = arg.lower().strip()
        if optiune in ("on", "off"):
            if not is_admin:
                _send_message(chat_id, "🔒 Doar Adminul poate porni/opri clasificatorul.")
                return
            monitor_state.set_classifier(optiune == "on")
            if optiune == "on":
                _send_message(chat_id,
                    "🧠 <b>Clasificator ACTIVAT.</b>\n"
                    "Produsele care nu sunt din categoriile tale (blistere, tin-uri, "
                    "accesorii) nu mai ajung la tine.")
            else:
                _send_message(chat_id, "⛔ <b>Clasificator DEZACTIVAT.</b> Primesti tot ca inainte.")
            return

        stat = classifier.statistici_cache()
        activ = "✅ ON" if monitor_state.is_classifier_enabled() else "⛔ OFF"
        reguli = classifier.incarca_reguli()
        nise = [k for k in reguli if not k.startswith("_")]
        _send_message(chat_id,
            f"🧠 <b>Clasificator:</b> {activ}" + chr(10) +
            f"📚 <b>Produse in cache:</b> {stat['total']} ({stat['relevante']} relevante)" + chr(10) +
            f"🏷 <b>Nise configurate:</b> {', '.join(nise) or 'niciuna'}" + chr(10) + chr(10) +
            "Sintaxa: <code>/classifier on</code> sau <code>/classifier off</code>")

    elif cmd == "/beta":
        optiune = arg.lower().strip()
        if optiune in ("on", "off"):
            if not is_admin:
                _send_message(chat_id, "🔒 Doar Adminul poate comuta modul beta.")
                return
            beta.seteaza(optiune == "on")
            if optiune == "on" and not beta.token_beta():
                _send_message(chat_id,
                    "⚠️ Am pornit modul beta, dar <code>TELEGRAM_BOT_TOKEN_BETA</code> lipseste din config/.env." + chr(10) +
                    "Vorbeste cu @BotFather, /newbot, si pune tokenul acolo. Pana atunci raman pe botul normal.")
            else:
                _send_message(chat_id, beta.raport())
            return
        _send_message(chat_id, beta.raport())
    elif cmd == "/preturi":
        _send_message(chat_id, price_check.raport())

    elif cmd == "/nise":
        _send_message(chat_id, policy.raport())

    elif cmd == "/feedback":
        _send_message(chat_id, feedback.raport())

    elif cmd == "/unblock":
        if not arg:
            _send_message(chat_id,
                "⚠️ Sintaxa: <code>/unblock pitch-black</code>\n"
                "Deblocheaza toate produsele al caror id contine textul dat.")
            return
        deblocate = feedback.deblocheaza(arg)
        if not deblocate:
            _send_message(chat_id, f"ℹ️ Niciun produs blocat nu contine '<code>{arg}</code>'.")
        else:
            lista = chr(10).join(f"  • <code>{d}</code>" for d in deblocate)
            _send_message(chat_id, f"🔓 <b>Deblocate {len(deblocate)}:</b>" + chr(10) + lista)

    # ── Info ──────────────────────────────────────────────────
    elif cmd == "/status":
        _send_message(chat_id, monitor_state.get_status_text())

    elif cmd == "/errors":
        _send_message(chat_id, monitor_state.get_errors_text())

    elif cmd == "/clearerrors":
        monitor_state.clear_errors()
        _send_message(chat_id, "🗑 <b>Log de erori șters!</b>")

    elif cmd == "/stats":
        _send_message(chat_id, monitor_state.get_stats_text())

    # ── Site-uri ──────────────────────────────────────────────
    elif cmd == "/sites":
        sites = _load_sites_config()
        muted = monitor_state.get_muted_copy()
        if not sites:
            _send_message(chat_id, "⚠️ Nu am putut citi sites_config.json.")
            return
        lines = ["🌐 <b>Site-uri configurate:</b>\n"]
        for s in sites:
            name = s["name"]
            icon = "🔇 <i>MUTED</i>" if name.lower() in muted else "✅ Activ"
            stats = monitor_state.site_stats.get(name)
            prod_info = f" | {stats['products']} produse" if stats else ""
            lines.append(f"{icon} — {name}{prod_info}")
        lines.append("\n💡 /mute &lt;nume&gt; / /unmute &lt;nume&gt;")
        _send_message(chat_id, "\n".join(lines))

    elif cmd == "/mute":
        if not arg:
            _send_message(chat_id, "⚠️ Specifică un site. Ex: <code>/mute noriel</code>")
            return
        sites   = _load_sites_config()
        matches = _find_site_match(arg, sites)
        if not matches:
            _send_message(chat_id,
                f"❌ Niciun site găsit cu '<code>{arg}</code>'.\n"
                "Verifică /sites pentru lista completă.")
        elif len(matches) > 1:
            names = "\n".join(f"  • {s['name']}" for s in matches)
            _send_message(chat_id, f"⚠️ Am găsit mai multe. Fii mai specific:\n{names}")
        else:
            site_name = matches[0]["name"]
            if monitor_state.is_muted(site_name):
                _send_message(chat_id, f"ℹ️ <b>{site_name}</b> e deja muted.")
            else:
                monitor_state.mute_site(site_name)
                _send_message(chat_id,
                    f"🔇 <b>{site_name}</b> — MUTED.\n"
                    "Nu îl mai verific. Scrie /unmute ca să îl reactivezi.")

    elif cmd == "/unmute":
        if not arg:
            _send_message(chat_id, "⚠️ Specifică un site. Ex: <code>/unmute noriel</code>")
            return
        sites   = _load_sites_config()
        matches = _find_site_match(arg, sites)
        if not matches:
            _send_message(chat_id,
                f"❌ Niciun site găsit cu '<code>{arg}</code>'.\n"
                "Verifică /sites pentru lista completă.")
        elif len(matches) > 1:
            names = "\n".join(f"  • {s['name']}" for s in matches)
            _send_message(chat_id, f"⚠️ Am găsit mai multe. Fii mai specific:\n{names}")
        else:
            site_name = matches[0]["name"]
            if not monitor_state.is_muted(site_name):
                _send_message(chat_id, f"ℹ️ <b>{site_name}</b> nu e muted.")
            else:
                monitor_state.unmute_site(site_name)
                _send_message(chat_id, f"🔔 <b>{site_name}</b> e din nou ACTIV!")

    elif cmd in ("/help", "/start"):
        _send_message(chat_id, HELP_TEXT)

    else:
        _send_message(chat_id,
            f"❓ Comandă necunoscută: <code>{text}</code>\n"
            "Scrie /help pentru lista de comenzi.")

# ─────────────────────────────────────────────────────────────────
#  Loop principal bot (rulează în thread daemon)
# ─────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
#  Apasarea butoanelor Good / Bad de sub notificari
# ─────────────────────────────────────────────────────────────
def _raspunde_callback(callback_id: str, text: str):
    """Telegram cere confirmarea apasarii, altfel butonul ramane in "loading"."""
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_token()}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text[:200]},
            timeout=10,
        )
    except Exception as e:
        print(f"⚠️ [Bot] Nu am putut confirma apasarea: {e}")


def _handle_callback(update: dict):
    """
    Trateaza apasarea pe Good / Bad.

    callback_data are forma  v:g:<token>  sau  v:b:<token>
    Tokenul e un hash scurt, pentru ca Telegram limiteaza campul la 64 de
    octeti, iar id-ul canonic poate fi mai lung.
    """
    callback = update.get("callback_query") or {}
    callback_id = callback.get("id", "")
    chat_id = str((callback.get("message") or {}).get("chat", {}).get("id", ""))
    data = callback.get("data", "")

    if chat_id not in ALLOWED_CHAT_IDS:
        _raspunde_callback(callback_id, "Acces neautorizat.")
        return

    if not data.startswith("v:"):
        _raspunde_callback(callback_id, "Buton necunoscut.")
        return

    bucati = data.split(":", 2)
    if len(bucati) != 3:
        _raspunde_callback(callback_id, "Buton invalid.")
        return

    _, litera, token = bucati
    verdict = "good" if litera == "g" else "bad"
    id_canonic = feedback.id_dupa_token(token)

    if not id_canonic:
        # Se poate intampla daca fisierul de verdicte a fost sters intre timp.
        _raspunde_callback(callback_id, "Nu mai stiu la ce produs se refera butonul.")
        return

    feedback.inregistreaza(id_canonic, verdict, chat_id=chat_id)

    if verdict == "bad":
        mesaj = f"⛔ Blocat: {id_canonic}. Nu mai primesti alerte pentru el, din niciun magazin."
    else:
        mesaj = f"✅ Notat ca bun: {id_canonic}."

    _raspunde_callback(callback_id, mesaj)
    print(f"🗳 [Bot] {chat_id} a votat {verdict.upper()} pentru {id_canonic}")

def _bot_loop():
    if not _token():
        print("⚠️ [Bot] Token Telegram lipsă — bot control dezactivat.")
        return

    if beta.e_activ():
        print("🧪 [Bot BETA] Pornit pe tokenul de test. Botul vechi e neatins.")
    else:
        print("🤖 [Bot] Pornit! Ascult comenzi Telegram...")
    offset = 0

    while True:
        try:
            updates = _get_updates(offset)
            for update in updates:
                offset  = update["update_id"] + 1

                # Apasare pe butoanele Good / Bad de sub o notificare.
                if "callback_query" in update:
                    _handle_callback(update)
                    continue

                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))
                text    = message.get("text", "")

                if not text.startswith("/"):
                    continue

                if chat_id not in ALLOWED_CHAT_IDS:
                    print(f"⚠️ [Bot] Mesaj ignorat de la chat_id necunoscut: {chat_id}")
                    _send_message(chat_id, "🔒 Acces neautorizat.")
                    continue

                print(f"🤖 [Bot] {chat_id}: {text}")
                _handle_command(chat_id, text)

        except Exception as e:
            print(f"⚠️ [Bot] Eroare în loop: {e}")
            time.sleep(5)

def start_bot_thread():
    """Pornește botul ca thread daemon și trimite mesaj de startup."""
    t = threading.Thread(target=_bot_loop, daemon=True, name="TelegramBot")
    t.start()

    # Mesaj de startup după 2s (să dăm timp botului să se conecteze)
    def _startup_msg():
        time.sleep(2)
        if ADMIN_ID:
            _send_message(ADMIN_ID,
                "🟢 <b>Pokemon Monitor PORNIT!</b>\n\n"
                "Monitorul a pornit și scanează acum.\n"
                "Scrie /help pentru lista de comenzi, /status pentru starea curentă."
            )
    threading.Thread(target=_startup_msg, daemon=True).start()

    return t

# Ultima alerta trimisa per magazin, ca sa nu repetam acelasi mesaj.
_ultima_alerta_site = {}
_magazine_cazute = set()
_ultima_alerta_agregata = 0.0
_lock_alerte_site = threading.Lock()

# Un magazin care oscileaza (fail, fail, fail, ok, fail, fail, fail...) reseteaza
# contorul de esecuri si ar trimite alerta la nesfarsit. Pe feed-ul real,
# Bookcity a trimis 5 mesaje identice. O alerta la 30 de minute e suficienta.
RACIRE_ALERTA_SITE = 30 * 60


def alert_site_failure(site_name: str, consecutive: int):
    """
    Chemat din main.py cand un magazin esueaza de mai multe ori la rand.

    Agregam: cu 21 de magazine, o alerta per magazin inseamna 21 de mesaje
    in cateva secunde. Strangem numele si trimitem UNUL singur.
    """
    if consecutive not in (3, 10):
        return

    acum = time.time()
    with _lock_alerte_site:
        _magazine_cazute.add(site_name)
        global _ultima_alerta_agregata
        if (acum - _ultima_alerta_agregata) < RACIRE_ALERTA_SITE:
            return
        _ultima_alerta_agregata = acum
        cazute = sorted(_magazine_cazute)
        _magazine_cazute.clear()

    if len(cazute) == 1:
        text = (f"⚠️ <b>{cazute[0]}</b> — esecuri repetate.\n"
                "Site-ul poate fi down, blocat, sau selectorul s-a schimbat.")
    else:
        lista = chr(10).join(f"  • {n}" for n in cazute[:12])
        text = (f"⚠️ <b>{len(cazute)} magazine cu esecuri repetate</b>" + chr(10) + lista)
        if len(cazute) > 12:
            text += chr(10) + f"  ... si inca {len(cazute)-12}"
        text += (chr(10) + chr(10) + "Daca sunt de pe acelasi domeniu, cel mai probabil "
                 "te limiteaza. Creste intervalul cu /interval.")

    _broadcast(text)
