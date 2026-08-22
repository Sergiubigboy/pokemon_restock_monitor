"""
Modul BETA — ruleaza versiunea noua in paralel cu cea veche, fara sa se calce.

DE CE E OBLIGATORIU UN TOKEN SEPARAT
────────────────────────────────────
Telegram livreaza fiecare update O SINGURA DATA, catre cine intreaba primul.
Daca doua procese fac getUpdates pe acelasi token, isi fura comenzile intre
ele: apesi /status si raspunde aleator unul din doi, sau niciunul. Nu e o
optimizare — fara token separat, ambii boti se strica.

CE FACE MODUL BETA
──────────────────
Cand e pornit, TOT procesul foloseste tokenul de beta: si trimiterea de
alerte, si ascultarea de comenzi. Botul vechi ramane pe tokenul lui,
neatins.

In plus, implicit trimite DOAR catre tine (admin). Canalul public si VIP-urile
nu primesc nimic de la versiunea de test — asta e tot rostul: testezi fara sa
deranjezi pe nimeni.

CONFIGURARE
───────────
1. Vorbeste cu @BotFather pe Telegram, /newbot, ia tokenul.
2. Pune-l in config/.env:      TELEGRAM_BOT_TOKEN_BETA=...
3. Porneste modul in config/beta.json:  {"beta": true}
   sau din Telegram cu /beta on

Fisierul beta.json e recitit la fiecare ciclu, deci comuti fara restart.
"""

import json
import logging
import os
import threading

BETA_FILE = os.path.join("config", "beta.json")

_lock = threading.RLock()
_config = None
_avertizat = False


def _incarca() -> dict:
    global _config
    if _config is None:
        try:
            with open(BETA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            _config = d if isinstance(d, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            _config = {}
        except Exception as e:
            logging.warning(f"⚠️ [Beta] Nu am putut citi {BETA_FILE}: {e}")
            _config = {}
    return _config


def reseteaza():
    """Hot-reload — chemat din bucla principala la fiecare ciclu."""
    global _config
    with _lock:
        _config = None


def token_beta() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN_BETA", "").strip()


def e_activ() -> bool:
    """
    True daca rulam in modul beta SI avem un token separat.

    Fara token separat refuzam sa pornim modul, altfel cei doi boti si-ar fura
    comenzile. Avertismentul se da o singura data.
    """
    global _avertizat
    with _lock:
        cerut = bool(_incarca().get("beta"))
    if not cerut:
        return False

    if not token_beta():
        if not _avertizat:
            logging.warning(
                "⚠️ [Beta] beta.json cere modul beta, dar TELEGRAM_BOT_TOKEN_BETA "
                "lipseste din config/.env. Raman pe botul normal — doua procese "
                "pe acelasi token si-ar fura comenzile."
            )
            _avertizat = True
        return False
    return True


def doar_admin() -> bool:
    """In beta, implicit nu trimitem pe canal si nici catre VIP-uri."""
    with _lock:
        return bool(_incarca().get("doar_admin", True))


def seteaza(activ: bool, doar_admin_val: bool = None):
    """Scrie beta.json. Folosit de comanda /beta din Telegram."""
    with _lock:
        d = dict(_incarca())
        d["beta"] = bool(activ)
        if doar_admin_val is not None:
            d["doar_admin"] = bool(doar_admin_val)
        try:
            os.makedirs(os.path.dirname(BETA_FILE), exist_ok=True)
            with open(BETA_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.warning(f"⚠️ [Beta] Nu am putut salva {BETA_FILE}: {e}")
            return False
    reseteaza()
    return True


def eticheta() -> str:
    """Prefixul pus in fata mesajelor, ca sa nu confunzi botii."""
    return "🧪 <b>[BETA]</b> " if e_activ() else ""


def raport() -> str:
    activ = e_activ()
    cerut = bool(_incarca().get("beta"))
    are_token = bool(token_beta())

    linii = [f"🧪 <b>Mod BETA:</b> {'✅ ACTIV' if activ else '⛔ oprit'}"]
    if cerut and not are_token:
        linii.append("\n⚠️ beta.json cere beta, dar <code>TELEGRAM_BOT_TOKEN_BETA</code> "
                     "lipseste din config/.env.")
        linii.append("Vorbeste cu @BotFather, /newbot, si pune tokenul acolo.")
    elif activ:
        linii.append(f"📨 Trimit doar catre admin: {'da' if doar_admin() else 'nu'}")
        linii.append("\nBotul vechi ruleaza mai departe pe tokenul lui, neatins.")
    else:
        linii.append("\nPorneste cu <code>/beta on</code> dupa ce ai pus "
                     "<code>TELEGRAM_BOT_TOKEN_BETA</code> in config/.env.")
    return "\n".join(linii)
