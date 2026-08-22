"""
Performanta per item de watchlist — datele pe care agentul saptamanal le
foloseste ca sa decida ce slot taie si ce slot elibereaza.

Fara asta, rotatia sloturilor e pe ghicite. Cu asta, agentul vede diferenta
dintre:
  - item care nu s-a potrivit NICIODATA  -> slot mort, cuvinte-cheie gresite
  - item potrivit des, respins mereu cu "peste plafon" -> pretul de revanzare
    sau plafonul sunt calibrate gresit
  - item care produce alerte -> slotul isi merita locul

Scrierile sunt TAMPONATE. In turbo mode botul trece prin sute de produse pe
minut; daca am scrie fisierul la fiecare potrivire, am distruge cardul SD al
Pi-ului prin write amplification. Acumulam in memorie si salvam la interval.

Botul scrie doar in config/item_performance.json. Nu atinge niciodata
watchlist.json — acela e proprietatea agentului saptamanal.
"""

import json
import logging
import os
import threading
import time
from datetime import date

ITEM_STATS_FILE = os.path.join("config", "item_performance.json")

# Cat de des salvam pe disk, in secunde. Un minut inseamna cel mult 1440
# scrieri pe zi — nesemnificativ pentru un card SD.
INTERVAL_SALVARE = 60

_lock = threading.RLock()
_memorie: dict | None = None
_ultima_salvare = 0.0
_modificat = False


# Categorii de respingere. Textul complet al motivului contine cifre
# ("pret 358 peste plafonul 325 RON"), deci ar genera mii de chei unice.
# Agentul are nevoie de tipar, nu de fiecare instanta.
_CATEGORII = (
    ("peste plafon", "peste_plafon"),
    ("profit net", "profit_sub_prag"),
    ("ROI", "roi_sub_prag"),
    ("lichiditate", "lichiditate_mica"),
    ("vechi de", "date_expirate"),
    ("checked_at", "date_lipsa"),
    ("nedetectabil", "pret_nedetectabil"),
    ("invalid", "pret_invalid"),
    ("limita zilnica", "limita_zilnica"),
    ("expirat", "item_expirat"),
    ("dezactivat", "item_dezactivat"),
    ("eveniment", "tip_eveniment"),
)


def categorie_respingere(motiv: str) -> str:
    """Transforma motivul detaliat intr-o categorie stabila."""
    if not motiv:
        return "altul"
    for fragment, eticheta in _CATEGORII:
        if fragment in motiv:
            return eticheta
    return "altul"


def _incarca() -> dict:
    try:
        with open(ITEM_STATS_FILE, "r", encoding="utf-8") as f:
            date_json = json.load(f)
        if isinstance(date_json, dict) and isinstance(date_json.get("items"), dict):
            return date_json
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception as e:
        logging.warning(f"⚠️ [ItemStats] Nu am putut citi {ITEM_STATS_FILE}: {e}")
    return {"updated_at": None, "items": {}}


def _stare() -> dict:
    global _memorie
    if _memorie is None:
        _memorie = _incarca()
    return _memorie


def _intrare(item_id: str) -> dict:
    items = _stare()["items"]
    if item_id not in items:
        items[item_id] = {
            "matches": 0,           # de cate ori s-a potrivit un produs
            "alerts": 0,            # din care au dus la alerta de cumparare
            "profit_potential_ron": 0.0,
            "first_seen": date.today().isoformat(),
            "last_match_at": None,
            "last_alert_at": None,
            "sites": {},            # pe ce magazine a aparut
            "rejects": {},          # categorie -> numar
        }
    return items[item_id]


def _poate_salva(fortat: bool = False):
    global _ultima_salvare, _modificat
    if not _modificat:
        return
    acum = time.time()
    if not fortat and (acum - _ultima_salvare) < INTERVAL_SALVARE:
        return

    stare = _stare()
    stare["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    temporar = ITEM_STATS_FILE + ".tmp"
    try:
        director = os.path.dirname(ITEM_STATS_FILE)
        if director:
            os.makedirs(director, exist_ok=True)
        with open(temporar, "w", encoding="utf-8") as f:
            json.dump(stare, f, ensure_ascii=False, indent=2)
        os.replace(temporar, ITEM_STATS_FILE)
        _ultima_salvare = acum
        _modificat = False
    except Exception as e:
        logging.warning(f"⚠️ [ItemStats] Nu am putut salva statisticile: {e}")


def record_item_alert(item_id: str, site_name: str, net_profit_ron):
    """Un item a produs o alerta reala de cumparare."""
    global _modificat
    if not item_id:
        return
    with _lock:
        intrare = _intrare(item_id)
        intrare["matches"] += 1
        intrare["alerts"] += 1
        intrare["last_match_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        intrare["last_alert_at"] = intrare["last_match_at"]
        intrare["profit_potential_ron"] = round(
            intrare["profit_potential_ron"] + float(net_profit_ron or 0), 2
        )
        intrare["sites"][site_name] = intrare["sites"].get(site_name, 0) + 1
        _modificat = True
        _poate_salva()


def record_item_reject(item_id: str, motiv: str):
    """Un item s-a potrivit dar a fost respins — contorizam de ce."""
    global _modificat
    if not item_id:
        return
    with _lock:
        intrare = _intrare(item_id)
        intrare["matches"] += 1
        intrare["last_match_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        eticheta = categorie_respingere(motiv)
        intrare["rejects"][eticheta] = intrare["rejects"].get(eticheta, 0) + 1
        _modificat = True
        _poate_salva()


def flush(fortat: bool = True):
    """Salvare imediata — chemata la finalul fiecarui ciclu de scanare."""
    with _lock:
        _poate_salva(fortat=fortat)


def raport(watchlist: dict | None = None) -> str:
    """Rezumat text pentru comanda Telegram /performance."""
    with _lock:
        stare = _stare()
        items = dict(stare.get("items", {}))

    if not items:
        return "📉 Nu există încă date de performanță. Lasă botul să ruleze."

    etichete = {}
    if watchlist:
        for i in watchlist.get("items", []):
            etichete[str(i.get("id"))] = f"[{i.get('tier', '?')}] {i.get('label', i.get('id'))}"

    linii = ["📉 <b>Performanță per item</b>\n"]
    for item_id, d in sorted(items.items(), key=lambda kv: -kv[1].get("alerts", 0)):
        nume = etichete.get(item_id, item_id)
        potriviri = d.get("matches", 0)
        alerte = d.get("alerts", 0)
        profit = d.get("profit_potential_ron", 0)

        linii.append(f"<b>{nume}</b>")
        linii.append(f"   {potriviri} potriviri → {alerte} alerte · {profit:.0f} RON potențial")

        respingeri = d.get("rejects", {})
        if respingeri:
            top = sorted(respingeri.items(), key=lambda kv: -kv[1])[:2]
            detaliu = ", ".join(f"{k} ×{v}" for k, v in top)
            linii.append(f"   respins mai ales: {detaliu}")
        if potriviri == 0:
            linii.append("   ⚠️ slot mort — nu s-a potrivit niciodată")

    return "\n".join(linii)
