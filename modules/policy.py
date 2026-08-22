"""
Motorul de decizie pe politica de nisa + inteligenta de set.

Inlocuieste logica de watchlist scrisa de mana. Combina doua intrebari:

    1. TIPUL conteaza pe nisa asta?     -> config/niche_policy.json
    2. SETUL merita?                     -> config/set_intelligence.json

Regula centrala, ceruta explicit: cand un set e bun (tier S), vrei TOT din el —
ETB, booster box, bundle, collection box. Nu alegi produs cu produs. De aceea
politica e pe tipuri, iar setul ridica sau coboara intensitatea alertei.

Cele trei rezultate posibile:

    CUMPARA      tip urmarit + set tier S/A   -> alerta tare, decizie deja luata
    SEMNALARE    tip urmarit + set necunoscut -> discret, fara urgenta
    TACERE       tip ignorat                  -> nu ajunge nicaieri

Un produs bun intra o singura data. De aceea decizia nu se calculeaza la
momentul drop-ului: tier-ul setului e stabilit cu saptamani inainte, prin
research, iar la drop se citeste doar.
"""

import json
import logging
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import date

NICHE_POLICY_FILE = os.path.join("config", "niche_policy.json")
SET_INTELLIGENCE_FILE = os.path.join("config", "set_intelligence.json")

# Cat de tare suna alerta, per tier de set.
_INTENSITATE = {"S": "CUMPARA", "A": "CUMPARA", "B": "SEMNALARE", "C": "TACERE"}

_lock = threading.RLock()
_politica = None
_seturi = None


@dataclass
class Verdict:
    """Ce face botul cu produsul asta."""
    actiune: str                  # "CUMPARA" | "SEMNALARE" | "TACERE"
    motiv: str = ""

    nisa: str = ""
    tip: str = ""
    set_: str = ""
    id_canonic: str = ""

    tier_set: str = ""
    categorie_set: str = ""
    explicatie_set: str = ""
    surse: list = field(default_factory=list)
    lanseaza_la: str = ""

    @property
    def e_alerta(self) -> bool:
        return self.actiune in ("CUMPARA", "SEMNALARE")

    @property
    def e_urgent(self) -> bool:
        return self.actiune == "CUMPARA"


def _normalizeaza(text: str) -> str:
    descompus = unicodedata.normalize("NFKD", str(text or ""))
    fara = "".join(c for c in descompus if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", fara).strip().lower()


def _incarca(cale: str, nume: str):
    try:
        with open(cale, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logging.warning(f"⚠️ [Policy] {cale} nu exista — {nume} indisponibil.")
    except json.JSONDecodeError as e:
        logging.error(f"❌ [Policy] JSON invalid in {cale}: {e}")
    except Exception as e:
        logging.error(f"❌ [Policy] Nu am putut citi {cale}: {e}")
    return {}


def incarca_politica(cale: str = None) -> dict:
    global _politica
    with _lock:
        if _politica is None:
            _politica = _incarca(cale or NICHE_POLICY_FILE, "politica de nisa")
        return _politica


def incarca_seturi(cale: str = None) -> dict:
    global _seturi
    with _lock:
        if _seturi is None:
            _seturi = _incarca(cale or SET_INTELLIGENCE_FILE, "inteligenta de set")
        return _seturi


def reseteaza():
    """Hot-reload si teste."""
    global _politica, _seturi
    with _lock:
        _politica = None
        _seturi = None


def nise_configurate() -> list:
    return [k for k in incarca_politica() if not k.startswith("_")]


def cauta_set(nisa: str, set_detectat: str) -> tuple:
    """
    Gaseste setul in registru. Intoarce (cheie, date) sau (None, {}).

    Potrivirea e pe continut, nu pe egalitate: clasificatorul poate scoate
    "mega evolution pitch black" iar registrul sa aiba "pitch black".
    """
    if not set_detectat:
        return None, {}

    seturi_nisa = incarca_seturi().get(nisa) or {}
    tinta = _normalizeaza(set_detectat)

    if tinta in seturi_nisa:
        return tinta, seturi_nisa[tinta]

    # Cel mai lung nume de set continut in textul detectat castiga, ca
    # "30th celebration" sa bata "30th" daca ambele ar exista.
    potriviri = [(k, v) for k, v in seturi_nisa.items()
                 if not k.startswith("_") and (_normalizeaza(k) in tinta or tinta in _normalizeaza(k))]
    if potriviri:
        potriviri.sort(key=lambda kv: -len(kv[0]))
        return potriviri[0]

    return None, {}


def decide(clasificare: dict, nisa: str, azi: date = None) -> Verdict:
    """
    Verdictul pentru un produs deja clasificat.

    `clasificare` e ce intoarce modules.classifier: {linie, set, tip, id_canonic}.
    """
    azi = azi or date.today()
    tip = _normalizeaza(clasificare.get("tip", ""))
    set_detectat = _normalizeaza(clasificare.get("set", ""))
    id_canonic = clasificare.get("id_canonic", "")

    v = Verdict(actiune="TACERE", nisa=nisa, tip=tip, set_=set_detectat,
                id_canonic=id_canonic)

    politica = incarca_politica().get(nisa)
    if not politica:
        # Nisa fara politica: nu filtram nimic, lasam fluxul clasic sa decida.
        v.actiune = "SEMNALARE"
        v.motiv = f"nisa '{nisa}' nu are politica definita"
        return v

    urmarite = set(politica.get("urmareste", []))
    ignorate = set(politica.get("ignora", []))

    # Editia se verifica prima: o cutie chinezeasca dintr-un set bun ramane
    # marfa fara piata in RO, oricat de bun ar fi setul.
    editie = _normalizeaza(clasificare.get("editie", "standard")) or "standard"
    acceptate = politica.get("editii_acceptate") or ["standard"]
    if editie not in acceptate:
        v.motiv = f"editie '{editie}' — fara piata secundara in RO"
        return v

    if tip in ignorate:
        v.motiv = f"tip '{tip}' ignorat pe nisa {nisa}"
        return v

    if tip in ("", "necunoscut"):
        # Nu stim ce e. Nu-l aruncam — poate fi exact drop-ul care conteaza.
        v.actiune = "SEMNALARE"
        v.motiv = "tip nedeterminat"
        return v

    if tip not in urmarite:
        v.motiv = f"tip '{tip}' nu e pe lista nisei {nisa}"
        return v

    # Tipul conteaza. Acum setul decide intensitatea.
    cheie, date_set = cauta_set(nisa, set_detectat)
    if not date_set:
        v.actiune = "SEMNALARE"
        v.motiv = "set necunoscut — nu stiu daca merita"
        return v

    tier = str(date_set.get("tier", "")).upper()
    v.tier_set = tier
    v.categorie_set = date_set.get("categorie", "")
    v.explicatie_set = date_set.get("motiv", "")
    v.surse = date_set.get("surse", []) or []
    v.lanseaza_la = date_set.get("lanseaza_la", "") or ""

    v.actiune = _INTENSITATE.get(tier, "SEMNALARE")
    if v.actiune == "CUMPARA":
        v.motiv = f"set '{cheie}' tier {tier}"
    elif v.actiune == "TACERE":
        v.motiv = f"set '{cheie}' marcat tier {tier} — {v.explicatie_set[:60]}"
    else:
        v.motiv = f"set '{cheie}' tier {tier or '?'}"

    return v


def seturi_expirate(azi: date = None) -> bool:
    """True daca registrul de seturi nu a mai fost actualizat de agent."""
    azi = azi or date.today()
    limita = (incarca_seturi().get("_meta") or {}).get("valid_until")
    if not limita:
        return False
    try:
        return azi > date.fromisoformat(str(limita)[:10])
    except ValueError:
        return False


def raport() -> str:
    """Text pentru comanda Telegram /nise."""
    politica = incarca_politica()
    seturi = incarca_seturi()

    linii = ["🎯 <b>Politica pe nișe</b>\n"]
    for nisa in nise_configurate():
        p = politica[nisa]
        s = {k: v for k, v in (seturi.get(nisa) or {}).items() if not k.startswith("_")}
        pe_tier = {}
        for date_set in s.values():
            t = str(date_set.get("tier", "?")).upper()
            pe_tier[t] = pe_tier.get(t, 0) + 1
        rezumat = " ".join(f"{t}:{n}" for t, n in sorted(pe_tier.items())) or "niciun set"
        linii.append(f"<b>{nisa}</b>")
        linii.append(f"   urmăresc: {', '.join(p.get('urmareste', []))}")
        linii.append(f"   seturi: {rezumat}")

    if seturi_expirate():
        linii.append("\n⚠️ <b>Registrul de seturi e EXPIRAT</b> — rulează agentul de research.")

    return "\n".join(linii)
