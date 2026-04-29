import json
import os
import time
import threading
import requests
from dotenv import load_dotenv
from modules.state_manager import load_muted_sites, save_muted_sites

load_dotenv(dotenv_path="config/.env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS_RAW       = os.getenv("TELEGRAM_CHAT_ID", "")
ALLOWED_CHAT_IDS   = set(id.strip() for id in CHAT_IDS_RAW.split(",") if id.strip())

# ─────────────────────────────────────────────────────────────────
#  Stare partajată cu main loop — toate operațiile sunt thread-safe
# ─────────────────────────────────────────────────────────────────
class MonitorState:
    def __init__(self):
        self._lock          = threading.Lock()
        self.turbo_mode     = False
        self.paused         = False
        self.debug_mode     = False
        self.check_interval = 300          # secunde — modificabil via /interval
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

        mode_str    = "⚡ TURBO (1s)" if turbo else f"🐢 Normal ({iv}s)"
        state_str   = "⏸ PAUZĂ" if paused else "▶️ Activ"
        debug_str   = "🔍 ON" if debug else "OFF"

        lines = [
            "📊 <b>Status Monitor</b>\n",
            f"📡 <b>Stare:</b>    {state_str}",
            f"🔄 <b>Mod:</b>      {mode_str}",
            f"🔍 <b>Debug:</b>    {debug_str}",
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
def _send_message(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
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
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
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
    "⏸ /pause — Pauză scanare\n"
    "▶️ /resume — Reia scanarea\n"
    "🔍 /debug — Toggle debug mode\n"
    "⏱ /interval &lt;sec&gt; — Seteaza intervalul (ex: /interval 60)\n\n"
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
                "Toate produsele valide vor fi retrimise pe Telegram la fiecare scan.")
        else:
            _send_message(chat_id, "🔍 <b>Debug MODE OFF.</b> Revenire la mod normal.")

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
def _bot_loop():
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ [Bot] Token Telegram lipsă — bot control dezactivat.")
        return

    print("🤖 [Bot] Pornit! Ascult comenzi Telegram...")
    offset = 0

    while True:
        try:
            updates = _get_updates(offset)
            for update in updates:
                offset  = update["update_id"] + 1
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
        _broadcast(
            "🟢 <b>Pokemon Monitor PORNIT!</b>\n\n"
            "Monitorul a pornit și scanează acum.\n"
            "Scrie /help pentru lista de comenzi, /status pentru starea curentă."
        )
    threading.Thread(target=_startup_msg, daemon=True).start()

    return t

def alert_site_failure(site_name: str, consecutive: int):
    """Chemat din main.py când un site eșuează de mai multe ori la rând."""
    if consecutive in (3, 10):
        _broadcast(
            f"⚠️ <b>ALERTĂ: {site_name}</b> — {consecutive} eșecuri consecutive!\n"
            "Site-ul poate fi down sau blocat. Verifică manual."
        )
