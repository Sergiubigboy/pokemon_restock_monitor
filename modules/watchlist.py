"""
Motorul de decizie pe watchlist.

Citeste config/watchlist.json (scris saptamanal de agentul separat — botul NU
scrie niciodata in el) si decide daca un produs gasit in stoc merita alerta de
CUMPARARE, pe baza aritmeticii de profit.

Botul nu estimeaza preturi de revanzare. Doar face aritmetica pe cifrele puse
de agent in watchlist si verifica prospetimea lor.

Singurul fisier scris de acest modul e config/alert_counts.json (contorul zilnic
de alerte per item).
"""

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

from modules.price_parser import parse_price_ron

WATCHLIST_FILE = os.path.join("config", "watchlist.json")
ALERT_COUNTS_FILE = os.path.join("config", "alert_counts.json")

# Ordinea de prioritate a tier-urilor: S bate A, A bate B etc.
_ORDINE_TIER = {"S": 0, "A": 1, "B": 2, "C": 3}


# ─────────────────────────────────────────────────────────────────
#  Rezultatul evaluarii
# ─────────────────────────────────────────────────────────────────
@dataclass
class Decision:
    """Tot ce trebuie sa stie apelantul ca sa decida si sa compuna mesajul."""

    should_alert: bool
    kind: str                      # "BUY" | "HEADS_UP" | "REJECT"
    reason: str                    # motivul respingerii (gol daca should_alert)

    item_id: str = ""
    label: str = ""
    niche: str = ""
    tier: str = ""

    price_raw: str = ""
    price_ron: float | None = None
    max_price_ron: float = 0.0

    resale_ron: float = 0.0
    resale_source: str = ""
    resale_age_days: int | None = None
    liquidity_30d: int = 0
    confidence: str = ""

    net_profit_ron: float | None = None
    roi_pct: float | None = None
    max_qty: int = 1
    total_profit_ron: float | None = None

    alerts_today: int = 0
    max_alerts_per_day: int = 0
    item: dict = field(default_factory=dict, repr=False)


# ─────────────────────────────────────────────────────────────────
#  Normalizare text
# ─────────────────────────────────────────────────────────────────
def normalize(text: str) -> str:
    """
    lowercase + fara diacritice + spatii normalizate.
    "Pokémon ETB Scarlet & Violet" -> "pokemon etb scarlet & violet"
    """
    if not text:
        return ""
    descompus = unicodedata.normalize("NFKD", str(text))
    fara_diacritice = "".join(c for c in descompus if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", fara_diacritice).strip().lower()


def _contine(text_normalizat: str, cuvant: str) -> bool:
    """
    Verifica daca un cuvant-cheie apare in text, cu granita la stanga.

    Granita evita potriviri accidentale: "etb" nu trebuie sa se potriveasca in
    "sketbook", iar "10316" nu trebuie sa se potriveasca in "103160".
    Granita din dreapta se aplica doar daca termenul se termina alfanumeric,
    ca sa functioneze prefixele de tip "st-" (care trebuie sa prinda "ST-01").
    """
    cuvant = normalize(cuvant)
    if not cuvant:
        return False

    tipar = r"(?<!\w)" + re.escape(cuvant)
    if cuvant[-1].isalnum():
        tipar += r"(?!\w)"
    return re.search(tipar, text_normalizat) is not None


# ─────────────────────────────────────────────────────────────────
#  Incarcare watchlist
# ─────────────────────────────────────────────────────────────────
# Ultima eroare raportata, ca sa nu umplem bot.log cu acelasi mesaj.
# main.py reincarca watchlist-ul la FIECARE ciclu (la 1-3 secunde in turbo),
# deci un fisier lipsa ar genera mii de linii identice pe zi.
_ultima_eroare: str | None = None


def _logheaza_o_data(mesaj: str, nivel=logging.ERROR):
    global _ultima_eroare
    if mesaj != _ultima_eroare:
        logging.log(nivel, mesaj)
        _ultima_eroare = mesaj


def load_watchlist(path: str = WATCHLIST_FILE) -> dict:
    """
    Incarca watchlist-ul. La orice problema returneaza {} si logheaza —
    apelantul continua cu comportamentul vechi, botul nu se opreste niciodata.
    """
    global _ultima_eroare
    try:
        with open(path, "r", encoding="utf-8") as f:
            date_json = json.load(f)
    except FileNotFoundError:
        _logheaza_o_data(f"⚠️ [Watchlist] Fisierul {path} nu exista — evaluarea e dezactivata.",
                         logging.WARNING)
        return {}
    except json.JSONDecodeError as e:
        _logheaza_o_data(f"❌ [Watchlist] JSON invalid in {path}: {e} — pastrez comportamentul vechi.")
        return {}
    except Exception as e:
        _logheaza_o_data(f"❌ [Watchlist] Nu am putut citi {path}: {e}")
        return {}

    if not isinstance(date_json, dict) or not isinstance(date_json.get("items"), list):
        _logheaza_o_data(f"❌ [Watchlist] Structura neasteptata in {path} (lipseste lista 'items').")
        return {}

    if _ultima_eroare is not None:
        logging.info(f"✅ [Watchlist] {path} s-a incarcat din nou corect.")
        _ultima_eroare = None

    return date_json


def watchlist_is_stale(watchlist: dict, azi: date | None = None) -> bool:
    """
    True daca _meta.valid_until a trecut — adica agentul saptamanal nu a mai
    rulat. Cifrele de revanzare devin nesigure, deci merita un avertisment.
    """
    valid_until = (watchlist.get("_meta") or {}).get("valid_until")
    limita = _to_date(valid_until)
    if limita is None:
        return False
    return (azi or date.today()) > limita


# ─────────────────────────────────────────────────────────────────
#  Potrivire produs -> item
# ─────────────────────────────────────────────────────────────────
def _site_permis(site_name: str, item: dict) -> bool:
    permise = (item.get("buy") or {}).get("sites") or []
    tinta = normalize(site_name)
    return any(normalize(s) == tinta for s in permise)


def _reguli_potrivesc(nume_normalizat: str, item: dict) -> bool:
    reguli = item.get("match") or {}
    include_all = reguli.get("include_all") or []
    include_any = reguli.get("include_any") or []
    exclude = reguli.get("exclude") or []

    if not include_all and not include_any:
        return False  # item fara reguli — nu potrivim tot ce misca

    if not all(_contine(nume_normalizat, k) for k in include_all):
        return False

    if include_any and not any(_contine(nume_normalizat, k) for k in include_any):
        return False

    if any(_contine(nume_normalizat, k) for k in exclude):
        return False

    return True


def match_item(product_name: str, site_name: str, watchlist: dict) -> dict | None:
    """
    Gaseste item-ul din watchlist care se potriveste cu produsul.

    Item-urile dezactivate (enabled=false) sunt sarite aici, ca sa nu blocheze
    prin prioritate de tier un item activ care s-ar fi potrivit si el.
    evaluate() reverifica oricum 'enabled', pentru apelurile directe.

    La potriviri multiple castiga tier-ul cel mai inalt (S > A > B > C);
    la tier egal castiga primul din fisier.
    """
    if not watchlist:
        return None

    nume_normalizat = normalize(product_name)
    if not nume_normalizat:
        return None

    candidati = []
    for pozitie, item in enumerate(watchlist.get("items") or []):
        if not isinstance(item, dict) or not item.get("enabled", False):
            continue
        if not _site_permis(site_name, item):
            continue
        if not _reguli_potrivesc(nume_normalizat, item):
            continue
        rang = _ORDINE_TIER.get(str(item.get("tier", "")).upper(), 99)
        candidati.append((rang, pozitie, item))

    if not candidati:
        return None

    candidati.sort(key=lambda c: (c[0], c[1]))
    return candidati[0][2]


# ─────────────────────────────────────────────────────────────────
#  Contor zilnic de alerte (singurul fisier scris de modul)
# ─────────────────────────────────────────────────────────────────
def _load_counts() -> dict:
    try:
        with open(ALERT_COUNTS_FILE, "r", encoding="utf-8") as f:
            date_json = json.load(f)
        if not isinstance(date_json, dict):
            return {}
        return date_json
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logging.warning(f"⚠️ [Watchlist] Nu am putut citi contorul de alerte: {e}")
        return {}


def _counts_de_azi(azi: date | None = None) -> dict:
    """Contoarele valabile azi. Daca fisierul e de ieri, se reseteaza."""
    azi_str = (azi or date.today()).isoformat()
    date_json = _load_counts()
    if date_json.get("date") != azi_str:
        return {}
    counts = date_json.get("counts")
    return counts if isinstance(counts, dict) else {}


def alerts_today(item_id: str, azi: date | None = None) -> int:
    """Cate alerte s-au trimis azi pentru acest item."""
    return int(_counts_de_azi(azi).get(item_id, 0))


def record_alert(item_id: str, azi: date | None = None) -> int:
    """
    Incrementeaza contorul DUPA ce alerta a fost efectiv trimisa.
    evaluate() doar citeste contorul, ca simularile si debug-ul sa nu consume cota.
    Scriere atomica: fisierul nu ramane corupt daca Pi-ul cade la restart.
    """
    azi_str = (azi or date.today()).isoformat()
    counts = _counts_de_azi(azi)
    counts[item_id] = int(counts.get(item_id, 0)) + 1

    temporar = ALERT_COUNTS_FILE + ".tmp"
    try:
        director = os.path.dirname(ALERT_COUNTS_FILE)
        if director:
            os.makedirs(director, exist_ok=True)
        with open(temporar, "w", encoding="utf-8") as f:
            json.dump({"date": azi_str, "counts": counts}, f, ensure_ascii=False, indent=2)
        os.replace(temporar, ALERT_COUNTS_FILE)
    except Exception as e:
        logging.warning(f"⚠️ [Watchlist] Nu am putut salva contorul de alerte: {e}")

    return counts[item_id]


# ─────────────────────────────────────────────────────────────────
#  Evaluare
# ─────────────────────────────────────────────────────────────────
def _to_date(valoare) -> date | None:
    """Accepta "2026-08-17" sau "2026-08-17T19:00:00+03:00". None daca nu se poate."""
    if not valoare or not isinstance(valoare, str):
        return None
    text = valoare.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def evaluate(product: dict, item: dict, watchlist: dict, azi: date | None = None) -> Decision:
    """
    Calculeaza profitul net si ROI-ul si decide daca merita alerta.

        net = resale.median_ron * (1 - platform_fee_pct) - shipping - pret_observat
        roi = net / pret_observat

    Nu incrementeaza contorul de alerte — asta face record_alert(), dupa trimitere.
    """
    azi = azi or date.today()
    defaults = watchlist.get("defaults") or {}
    buy = item.get("buy") or {}
    resale = item.get("resale") or {}
    thresholds = item.get("thresholds") or {}

    comision = float(defaults.get("platform_fee_pct", 0) or 0)
    livrare = float(item.get("shipping_cost_ron", defaults.get("shipping_cost_ron", 0)) or 0)
    max_alerte = int(defaults.get("max_alerts_per_item_per_day", 0) or 0)
    min_lichiditate = float(defaults.get("min_liquidity_30d", 0) or 0)
    stale_after = int(defaults.get("stale_after_days", 0) or 0)

    min_profit = float(thresholds.get("min_profit_ron", defaults.get("min_profit_ron", 0)) or 0)
    min_roi = float(thresholds.get("min_roi_pct", defaults.get("min_roi_pct", 0)) or 0)

    pret_brut = product.get("price") or ""
    pret = parse_price_ron(pret_brut)
    revanzare = float(resale.get("median_ron", 0) or 0)
    lichiditate = float(resale.get("liquidity_30d", 0) or 0)
    verificat_la = _to_date(resale.get("checked_at"))
    vechime = (azi - verificat_la).days if verificat_la else None
    plafon = float(buy.get("max_price_ron", 0) or 0)
    item_id = str(item.get("id", ""))
    trimise_azi = alerts_today(item_id, azi)

    decizie = Decision(
        should_alert=False,
        kind="REJECT",
        reason="",
        item_id=item_id,
        label=str(item.get("label", "")),
        niche=str(item.get("niche", "")),
        tier=str(item.get("tier", "")),
        price_raw=str(pret_brut),
        price_ron=pret,
        max_price_ron=plafon,
        resale_ron=revanzare,
        resale_source=str(resale.get("source", "")),
        resale_age_days=vechime,
        liquidity_30d=int(lichiditate),
        confidence=str(resale.get("confidence", "")),
        max_qty=int(buy.get("max_qty_per_drop", 1) or 1),
        alerts_today=trimise_azi,
        max_alerts_per_day=max_alerte,
        item=item,
    )

    # ── Verificari structurale, inaintea oricarui calcul ──────────
    # Se fac primele ca sa nu logam avertismente de pret pentru item-uri care
    # oricum nu produc alerte de cumparare.
    if not item.get("enabled", False):
        decizie.reason = "item dezactivat (enabled=false)"
        return decizie

    expira = _to_date(item.get("expires_at"))
    if expira is not None and expira < azi:
        decizie.reason = f"item expirat la {expira.isoformat()}"
        return decizie

    if plafon <= 0:
        # Conventia din watchlist: max_price_ron=0 inseamna item monitorizat ca
        # EVENIMENT, nu ca achizitie directa (vezi nota de la lego-gwp).
        # Aici pretul 0 e normal (produsul chiar e gratuit), deci verificarea
        # asta trebuie sa vina inaintea celei de pret.
        decizie.kind = "HEADS_UP"
        decizie.reason = "item de tip eveniment (max_price_ron=0), nu alerta de cumparare"
        return decizie

    # ── Pretul: fara el nu putem calcula nimic ────────────────────
    if pret is None:
        decizie.reason = "pret nedetectabil — verifica selectorul de pret"
        logging.warning(
            f"🧨 [Watchlist] Pret nedetectabil pentru '{product.get('name', '?')}' "
            f"(text brut: '{pret_brut}') — item {item_id}. Selectorul de pret s-a stricat?"
        )
        return decizie

    if pret <= 0:
        decizie.reason = f"pret invalid ({pret})"
        return decizie

    # ── Aritmetica de profit ──────────────────────────────────────
    net = revanzare * (1 - comision) - livrare - pret
    roi = net / pret
    decizie.net_profit_ron = net
    decizie.roi_pct = roi
    decizie.total_profit_ron = net * decizie.max_qty

    # ── Praguri de calitate a datelor si de profitabilitate ───────
    if verificat_la is None:
        decizie.reason = "resale.checked_at lipseste sau e invalid"
        return decizie

    if stale_after and vechime is not None and vechime > stale_after:
        decizie.reason = f"pret de revanzare vechi de {vechime} zile (limita {stale_after})"
        return decizie

    if lichiditate < min_lichiditate:
        decizie.reason = f"lichiditate {int(lichiditate)}/30z sub pragul {int(min_lichiditate)}"
        return decizie

    if pret > plafon:
        decizie.reason = f"pret {pret:.0f} peste plafonul {plafon:.0f} RON"
        return decizie

    if net < min_profit:
        decizie.reason = f"profit net {net:.0f} sub pragul {min_profit:.0f} RON"
        return decizie

    if roi < min_roi:
        decizie.reason = f"ROI {roi * 100:.0f}% sub pragul {min_roi * 100:.0f}%"
        return decizie

    if max_alerte and trimise_azi >= max_alerte:
        decizie.reason = f"limita zilnica atinsa ({trimise_azi}/{max_alerte} alerte azi)"
        return decizie

    decizie.should_alert = True
    decizie.kind = "BUY"
    decizie.reason = ""
    return decizie
