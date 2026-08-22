"""
Verdictele tale: Good / Bad pe produse, pastrate permanent.

Cheia e id_canonic, nu numele brut. Acelasi produs se numeste altfel la
fiecare magazin, deci un "Bad" pe numele Noriel nu ar opri notificarile de la
Krit. Cu id canonic (pokemon|pitch-black|etb) un singur apas il opreste peste
tot, definitiv.

Fisierul asta e si memoria pe termen lung a sistemului. Pe o nisa noua nu poti
avea incredere in clasificare din ziua 1 — dar fiecare Good/Bad e un vot
salvat, iar dupa cateva saptamani ai istoric real despre ce merita si ce nu.

Telegram limiteaza callback_data la 64 de octeti, iar un id canonic poate fi
mai lung. Tinem un mic dictionar token -> id_canonic; tokenul e un hash scurt.
"""

import hashlib
import json
import logging
import os
import threading
import time

FEEDBACK_FILE = os.path.join("config", "rejected_products.json")

_lock = threading.RLock()
_date = None


def _structura_goala() -> dict:
    return {"verdicte": {}, "tokenuri": {}}


def _incarca() -> dict:
    global _date
    if _date is None:
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                incarcat = json.load(f)
            if isinstance(incarcat, dict) and "verdicte" in incarcat:
                _date = incarcat
                _date.setdefault("tokenuri", {})
            else:
                _date = _structura_goala()
        except (FileNotFoundError, json.JSONDecodeError):
            _date = _structura_goala()
        except Exception as e:
            logging.warning(f"⚠️ [Feedback] Nu am putut citi {FEEDBACK_FILE}: {e}")
            _date = _structura_goala()
    return _date


def reseteaza():
    """Pentru teste — forteaza recitirea de pe disk."""
    global _date
    with _lock:
        _date = None


def _salveaza():
    try:
        temporar = FEEDBACK_FILE + ".tmp"
        director = os.path.dirname(FEEDBACK_FILE)
        if director:
            os.makedirs(director, exist_ok=True)
        with open(temporar, "w", encoding="utf-8") as f:
            json.dump(_incarca(), f, ensure_ascii=False, indent=2)
        os.replace(temporar, FEEDBACK_FILE)
    except Exception as e:
        logging.warning(f"⚠️ [Feedback] Nu am putut salva verdictele: {e}")


def token_pentru(id_canonic: str) -> str:
    """
    Token scurt si stabil pentru callback_data (max 64 octeti in Telegram).
    Il memoram ca sa putem reface id-ul canonic cand vine apasarea butonului.
    """
    if not id_canonic:
        return ""
    scurt = hashlib.sha1(id_canonic.encode("utf-8")).hexdigest()[:10]
    with _lock:
        date = _incarca()
        if date["tokenuri"].get(scurt) != id_canonic:
            date["tokenuri"][scurt] = id_canonic
            _salveaza()
    return scurt


def id_dupa_token(token: str):
    with _lock:
        return _incarca()["tokenuri"].get(token)


def este_respins(id_canonic: str) -> bool:
    """True daca ai apasat Bad pe produsul asta."""
    if not id_canonic:
        return False
    with _lock:
        intrare = _incarca()["verdicte"].get(id_canonic)
    return bool(intrare) and intrare.get("verdict") == "bad"


def inregistreaza(id_canonic: str, verdict: str, nume_exemplu: str = "", chat_id: str = ""):
    """
    Salveaza un verdict. `verdict` e "good" sau "bad".
    Reapasarea suprascrie — te poti razgandi oricand.
    """
    if not id_canonic or verdict not in ("good", "bad"):
        return None

    # Garda de siguranta: un id cu doua bucati ("pokemon|booster-box") e o
    # CATEGORIE, nu un produs. Blocarea lui ar taia tacut zeci de produse bune.
    # S-a intamplat pe 19 august si a blocat toate booster box-urile Pokemon.
    if len(id_canonic.split("|")) < 3:
        logging.warning(
            f"⚠️ [Feedback] Refuz verdictul pe '{id_canonic}' — e o categorie "
            "intreaga, nu un produs. Ar bloca prea mult."
        )
        return None

    with _lock:
        date = _incarca()
        intrare = date["verdicte"].get(id_canonic, {"good": 0, "bad": 0})
        intrare["verdict"] = verdict
        intrare[verdict] = int(intrare.get(verdict, 0)) + 1
        intrare["ultima_data"] = time.strftime("%Y-%m-%d %H:%M:%S")
        intrare["de_catre"] = str(chat_id)
        if nume_exemplu:
            intrare["nume_exemplu"] = nume_exemplu
        date["verdicte"][id_canonic] = intrare
        _salveaza()
    return intrare


def raport() -> str:
    """Text pentru comanda Telegram /feedback."""
    with _lock:
        verdicte = dict(_incarca()["verdicte"])

    if not verdicte:
        return ("🗳 Niciun verdict inca.\n\n"
                "Apasa <b>Bad</b> sub o notificare ca sa nu mai primesti "
                "produsul acela din niciun magazin.")

    rele = [(k, v) for k, v in verdicte.items() if v.get("verdict") == "bad"]
    bune = [(k, v) for k, v in verdicte.items() if v.get("verdict") == "good"]

    linii = [f"🗳 <b>Verdictele tale</b> — {len(bune)} good, {len(rele)} bad\n"]

    if rele:
        linii.append("⛔ <b>Blocate (nu mai primesti alerte):</b>")
        for id_canonic, v in sorted(rele)[:25]:
            linii.append(f"  <code>{id_canonic}</code>")
        if len(rele) > 25:
            linii.append(f"  ... si inca {len(rele) - 25}")

    if bune:
        linii.append("\n✅ <b>Confirmate ca bune:</b>")
        for id_canonic, v in sorted(bune)[:15]:
            linii.append(f"  <code>{id_canonic}</code>")

    linii.append("\n💡 /unblock &lt;id&gt; ca sa deblochezi un produs.")
    return "\n".join(linii)


def deblocheaza(fragment: str) -> list:
    """
    Scoate din lista de blocate toate id-urile care contin `fragment`.
    Intoarce lista celor deblocate.
    """
    fragment = (fragment or "").strip().lower()
    if not fragment:
        return []

    with _lock:
        date = _incarca()
        potriviri = [k for k, v in date["verdicte"].items()
                     if v.get("verdict") == "bad" and fragment in k.lower()]
        for k in potriviri:
            del date["verdicte"][k]
        if potriviri:
            _salveaza()
    return potriviri
