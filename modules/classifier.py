"""
Clasificarea produselor: ce ESTE fiecare produs si care e identitatea lui
canonica, independenta de cum il scrie fiecare magazin.

Doua straturi, in ordinea asta:

1. LOCAL, pe reguli — gratuit, instant, functioneaza offline.
   Numele contin aproape mereu tipul explicit: "Elite Trainer Box", "Booster
   Display", "Blister", "Tin", "World Championship Decks". Nu ai nevoie de un
   model ca sa citesti asta. Acopera marea majoritate a cazurilor.

2. GEMINI, doar pentru ce a ramas neclar — si doar o data per nume, cu cache
   permanent pe disk.

De ce contreaza ordinea: cu stratul local, filtrarea merge din prima secunda,
fara cheie API si fara sa astepti reteaua in bucla de scanare. Modelul se
ocupa doar de resturi.

De ce id canonic
────────────────
Acelasi produs arata complet diferit de la magazin la magazin:
    Noriel : "Set carti de joc, Pokemon TCG, Mega Evolution, Pitch Black, ETB"
    Krit   : "Pokemon TCG: ME05 - Pitch Black - Elite Trainer Box"
Daca lista de "nu-mi mai trimite asta" ar fi pe numele brut, ai bloca produsul
la un magazin si l-ai primi in continuare de la celelalte. Cu id canonic
(pokemon|pitch-black|etb) un singur "Bad" il opreste peste tot.
"""

import json
import logging
import os
import re
import threading
import time
import unicodedata

import requests

CLASIFICARI_FILE = os.path.join("config", "product_classifications.json")
NICHE_RULES_FILE = os.path.join("config", "niche_rules.json")

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_NUME_PER_APEL = 40
TIMEOUT_APEL = 45

_lock = threading.RLock()
_cache = None
_reguli = None

NECUNOSCUT = {
    "linie": "necunoscut",
    "set": "",
    "tip": "necunoscut",
    "id_canonic": "",
    "relevant": None,
    "sursa": "niciuna",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "produse": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "linie": {"type": "string"},
                    "set": {"type": "string"},
                    "tip": {"type": "string"},
                    "relevant": {"type": "boolean"},
                },
                "required": ["index", "linie", "set", "tip", "relevant"],
            },
        }
    },
    "required": ["produse"],
}


def _normalizeaza(text: str) -> str:
    descompus = unicodedata.normalize("NFKD", str(text or ""))
    fara = "".join(c for c in descompus if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", fara).strip().lower()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _normalizeaza(text)).strip("-")


def _contine(text: str, cuvant: str) -> bool:
    """Potrivire cu granita de cuvant, ca "etb" sa nu prinda in "sketbook"."""
    tipar = r"(?<!\w)" + re.escape(cuvant)
    if cuvant and cuvant[-1].isalnum():
        tipar += r"(?!\w)"
    return re.search(tipar, text) is not None


# ─────────────────────────────────────────────────────────────────
#  STRATUL 1 — detectare locala, pe reguli
# ─────────────────────────────────────────────────────────────────
# Ordinea conteaza: cele mai specifice primele. "booster box" trebuie testat
# inaintea lui "booster", altfel orice display ar fi clasificat ca pachet.
_TIPARE_TIP = [
    ("upc",             ["ultra premium collection", "upc"]),
    ("etb",             ["elite trainer box", "etb"]),
    # MTG: marja e in Collector Boosters, nu in Play Boosters. Trebuie
    # distinse INAINTE de "booster box", altfel amandoua cad in acelasi cos.
    ("collector_booster_box", ["collector booster box", "collector booster display",
                               "collector box", "collector booster"]),
    ("play_booster_box",      ["play booster box", "play booster display",
                               "play booster"]),
    ("booster_box",     ["booster box", "booster display", "display box",
                         "booster bundle box"]),
    ("booster_bundle",  ["booster bundle", "3 pachete", "set 3 pachete",
                         "triple pack", "3-pack"]),
    ("bundle",          ["bundle"]),
    ("blister",         ["blister", "checklane"]),
    ("tin",             ["tin", "poke ball tin", "mini tins", "mini tin"]),
    ("battle_deck",     ["battle deck", "league battle", "championship decks",
                         "world champions decks", "starter deck", "theme deck"]),
    # "Vault" (Riftbound), "Bundle" (MTG), "Collection" simplu (Pokemon
    # Celebrations) — toate erau clasificate "altul" si taiate tacut, desi
    # sunt produse sigilate care conteaza. Vazute in log pe 23 august.
    ("collection_box",  ["premium collection", "ex box", "illustration collection",
                         "booster collection", "special collection", "collection box",
                         "vault", "gift bundle", "bundle gift", "collection -",
                         "collection:", "celebrations collection"]),
    ("accesoriu",       ["sleeve", "binder", "portfolio", "playmat", "album",
                         "deck box", "breloc", "husa"]),
    ("plus",            ["plus ", "plush", "jucarie de plus"]),
    ("single_card",     ["carte single", "single card", "graded card"]),
    # Cel mai general la final: un "booster" ramas dupa toate testele de mai
    # sus e un pachet simplu.
    ("booster_pack",    ["booster", "pachet booster", "expansion pack"]),
    ("set_lego",        ["lego"]),
    ("gwp",             ["gift with purchase", "gwp", "insiders reward"]),
]

# Numele de seturi cunoscute, pentru id-ul canonic local. Lista poate fi
# extinsa din config/niche_rules.json de catre agentul saptamanal, fara cod.
_SETURI_IMPLICITE = [
    "pitch black", "chaos rising", "ascended heroes", "perfect order",
    "destined rivals", "surging sparks", "stellar crown", "temporal forces",
    "abyss eye", "first partner", "30th celebration",
    "delta reign", "twilight masquerade", "paldean fates", "prismatic evolutions",
]


# Editii regionale. Cele asiatice au piata secundara complet separata si
# lichiditate aproape zero in RO — sunt zgomot curat pentru un scalper de aici.
_TIPARE_EDITIE = [
    ("chineza", ["editie chineza", "editie chinezeasca", "chinese edition",
                 "s-chn", "chn", "csv", "cbb", "editie chinez"]),
    ("coreana", ["korean edition", "editie coreana", "kor"]),
    ("japoneza", ["japanese edition", "editie japoneza", "pokemon jp", "- jp",
                  "(jp)", "japoneza"]),
]


def detecteaza_editie_local(nume: str) -> str:
    """Editia regionala. "standard" daca nu se detecteaza nimic exotic."""
    text = _normalizeaza(nume)
    for editie, cuvinte in _TIPARE_EDITIE:
        for cuvant in cuvinte:
            if _contine(text, cuvant):
                return editie
    return "standard"


def detecteaza_tip_local(nume: str) -> str:
    """Tipul produsului, doar din text. "necunoscut" daca nu e clar."""
    text = _normalizeaza(nume)
    if not text:
        return "necunoscut"
    for tip, cuvinte in _TIPARE_TIP:
        for cuvant in cuvinte:
            if _contine(text, cuvant):
                return tip
    return "necunoscut"


def seturi_cunoscute(nisa: str) -> list:
    """
    Numele de seturi pe care le poate detecta clasificatorul, pentru o nisa.

    Sursa principala e config/set_intelligence.json — adica exact ce a gasit
    agentul de research. Asa, un set nou aflat duminica devine automat
    detectabil luni, fara sa atinga nimeni codul.
    """
    din_research = []
    try:
        from modules import policy
        din_research = [k for k in (policy.incarca_seturi().get(nisa) or {})
                        if not k.startswith("_")]
    except Exception:
        pass

    # Seturile din research au PRIORITATE fata de lista implicita. Un nume ca
    # "Mega Evolution, Pitch Black, Elite Trainer Box" contine si era, si setul;
    # setul cercetat e cel care conteaza, indiferent care e mai lung.
    # In fiecare grup, cele mai lungi primele ("30th celebration" bate "30th").
    rezerva = [x for x in _SETURI_IMPLICITE if x not in din_research]
    return (sorted(din_research, key=len, reverse=True)
            + sorted(rezerva, key=len, reverse=True))


_RE_COD_LEGO = re.compile(r"(?<!\d)(\d{5})(?!\d)")


def detecteaza_set_local(nume: str, seturi=None, nisa: str = "") -> str:
    """Numele setului, daca apare in text. Sir gol altfel."""
    text = _normalizeaza(nume)

    # LEGO are cel mai curat identificator din tot sistemul: numarul de set,
    # 5 cifre. Daca apare, bate orice potrivire pe nume.
    if nisa == "LEGO" or _contine(text, "lego"):
        cod = _RE_COD_LEGO.search(text)
        if cod:
            return cod.group(1)

    lista = seturi if seturi is not None else seturi_cunoscute(nisa)
    for s in lista:
        if _contine(text, _normalizeaza(s)):
            return _normalizeaza(s)
    return ""


def detecteaza_linie_local(nume: str, nisa: str) -> str:
    text = _normalizeaza(nume)
    for linie in ("pokemon", "one piece", "lego", "magic", "riftbound",
                  "jellycat", "hot wheels"):
        if _contine(text, linie):
            return linie
    return _normalizeaza(nisa).split()[0] if nisa else "necunoscut"


def construieste_id(linie: str, set_: str, tip: str, nume_rezerva: str = "") -> str:
    """
    Id canonic. NU are voie sa iasa vreodata din doua bucati.

    Bug reparat 19 august: cand setul nu era recunoscut, id-ul devenea
    "pokemon|booster-box" — adica o CATEGORIE intreaga, nu un produs. Un Bad
    apasat pe o cutie chinezeasca a blocat toate booster box-urile Pokemon
    ale caror seturi nu erau in lista, inclusiv unele bune. Pierdere tacuta.

    Fara set recunoscut folosim numele produsului: blocarea e mai ingusta
    (nu se generalizeaza intre magazine), dar nu poate face rau.
    """
    discriminant = _slug(set_) if set_ else ""
    if not discriminant:
        discriminant = _slug(nume_rezerva)[:60] or "necunoscut"
    return "|".join([_slug(linie) or "necunoscut", discriminant, _slug(tip) or "altul"])


def clasifica_local(nume: str, nisa: str) -> dict:
    """
    Verdict fara niciun apel de retea.

    tip="necunoscut" inseamna "trimite-l la Gemini"; orice altceva e final.
    """
    reguli_nisa = incarca_reguli().get(nisa) or {}
    seturi = reguli_nisa.get("seturi_cunoscute")
    relevante = set(reguli_nisa.get("relevante", []))

    tip = detecteaza_tip_local(nume)
    linie = detecteaza_linie_local(nume, nisa)
    set_ = detecteaza_set_local(nume, seturi, nisa)
    editie = detecteaza_editie_local(nume)

    if tip == "necunoscut":
        # Tot construim un id, ca butonul Bad sa functioneze si aici. Fara set
        # cunoscut, folosim numele normalizat — blocheaza cel putin produsul
        # asta, chiar daca nu se generalizeaza intre magazine.
        rezerva = set_ or _slug(nume)[:60]
        return {
            "linie": linie,
            "set": set_,
            "tip": "necunoscut",
            "id_canonic": construieste_id(linie, rezerva, "necunoscut", nume),
            "relevant": None,
            "editie": editie,
            "sursa": "local",
        }

    return {
        "linie": linie,
        "set": set_,
        "tip": tip,
        "id_canonic": construieste_id(linie, set_, tip, nume),
        "relevant": tip in relevante,
        "editie": editie,
        "sursa": "local",
    }


# ─────────────────────────────────────────────────────────────────
#  Reguli per nisa
# ─────────────────────────────────────────────────────────────────
def incarca_reguli(cale: str = None) -> dict:
    global _reguli
    with _lock:
        if _reguli is None:
            try:
                with open(cale or NICHE_RULES_FILE, "r", encoding="utf-8") as f:
                    _reguli = json.load(f)
            except Exception as e:
                logging.warning(f"⚠️ [Classifier] Nu am putut citi regulile de nisa: {e}")
                _reguli = {}
        return _reguli


def reseteaza_reguli():
    global _reguli
    with _lock:
        _reguli = None


def tipuri_relevante(nisa: str) -> list:
    return (incarca_reguli().get(nisa) or {}).get("relevante", [])


# ─────────────────────────────────────────────────────────────────
#  Cache pe disk
# ─────────────────────────────────────────────────────────────────
def _incarca_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(CLASIFICARI_FILE, "r", encoding="utf-8") as f:
                date = json.load(f)
            _cache = date if isinstance(date, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = {}
        except Exception as e:
            logging.warning(f"⚠️ [Classifier] Nu am putut citi cache-ul: {e}")
            _cache = {}
    return _cache


def reseteaza_cache():
    global _cache
    with _lock:
        _cache = None


def _salveaza_cache():
    try:
        temporar = CLASIFICARI_FILE + ".tmp"
        director = os.path.dirname(CLASIFICARI_FILE)
        if director:
            os.makedirs(director, exist_ok=True)
        with open(temporar, "w", encoding="utf-8") as f:
            json.dump(_incarca_cache(), f, ensure_ascii=False, indent=2)
        for incercare in range(5):
            try:
                os.replace(temporar, CLASIFICARI_FILE)
                return
            except PermissionError:
                if incercare == 4:
                    raise
                time.sleep(0.1 * (incercare + 1))
    except Exception as e:
        logging.warning(f"⚠️ [Classifier] Nu am putut salva cache-ul: {e}")


def _cheie(nume: str, nisa: str) -> str:
    return _normalizeaza(nisa) + "||" + _normalizeaza(nume)


def din_cache(nume: str, nisa: str):
    with _lock:
        return _incarca_cache().get(_cheie(nume, nisa))


def statistici_cache() -> dict:
    with _lock:
        cache = _incarca_cache()
        relevante = sum(1 for v in cache.values() if v.get("relevant") is True)
        return {"total": len(cache), "relevante": relevante}


# ─────────────────────────────────────────────────────────────────
#  STRATUL 2 — Gemini, doar pentru ce a ramas neclar
# ─────────────────────────────────────────────────────────────────
def _construieste_prompt(nume_lista: list, nisa: str) -> str:
    reguli = incarca_reguli().get(nisa) or {}
    relevante = reguli.get("relevante", [])
    ignorate = reguli.get("ignorate", [])
    nota = reguli.get("nota", "")

    linii_produse = "\n".join(f"{i}. {n}" for i, n in enumerate(nume_lista))
    toate_tipurile = ", ".join(relevante + ignorate + ["altul"])
    context = f"Context despre nisa: {nota}" if nota else ""

    return (
        f'Clasifici produse dintr-un magazin online romanesc, nisa "{nisa}".\n\n'
        "Pentru fiecare produs din lista, intoarce:\n"
        "- index: numarul din lista\n"
        '- linie: linia de produs, cu litere mici (ex: "pokemon", "one piece")\n'
        "- set: numele setului/editiei, cu litere mici, fara tipul produsului\n"
        '       (ex: "pitch black"). Sir gol daca nu se poate deduce.\n'
        f"- tip: EXACT una din valorile: {toate_tipurile}\n"
        f"- relevant: true DOAR daca tip e una dintre: {', '.join(relevante)}\n\n"
        f"{context}\n\n"
        "Reguli:\n"
        "- numele sunt in romana sau engleza, adesea prost formatate\n"
        "- nu inventa seturi; daca nu esti sigur, lasa sirul gol\n"
        "- fii strict: daca nu e clar un tip relevant, pune relevant=false\n\n"
        "Produse:\n"
        f"{linii_produse}\n"
    )


def _apeleaza_gemini(nume_lista: list, nisa: str, cheie_api: str) -> list:
    endpoint = ("https://generativelanguage.googleapis.com/v1beta/models/"
                f"{MODEL}:generateContent")
    corp = {
        "contents": [{"parts": [{"text": _construieste_prompt(nume_lista, nisa)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
        },
    }
    raspuns = requests.post(
        endpoint,
        headers={"x-goog-api-key": cheie_api, "Content-Type": "application/json"},
        json=corp,
        timeout=TIMEOUT_APEL,
    )
    if raspuns.status_code != 200:
        raise RuntimeError(f"HTTP {raspuns.status_code}: {raspuns.text[:200]}")
    date = raspuns.json()
    text = date["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text).get("produse", [])


def clasifica(nume_lista: list, nisa: str, cheie_api=None, foloseste_llm=True) -> dict:
    """
    Clasifica o lista de nume. Intoarce {nume: verdict}.

    Ordinea: cache -> reguli locale -> Gemini (doar pentru ce a ramas neclar).
    """
    if not nume_lista:
        return {}

    if cheie_api is None:
        cheie_api = os.getenv("GEMINI_API_KEY", "").strip()

    rezultat = {}
    de_intrebat = []

    with _lock:
        cache = _incarca_cache()
        for nume in nume_lista:
            gasit = cache.get(_cheie(nume, nisa))
            if gasit is not None:
                rezultat[nume] = gasit
                continue

            local = clasifica_local(nume, nisa)
            rezultat[nume] = local
            if local["tip"] == "necunoscut":
                de_intrebat.append(nume)
            else:
                # Verdictele locale sigure intra si ele in cache.
                cache[_cheie(nume, nisa)] = local
        if len(cache) > 0:
            _salveaza_cache()

    if not de_intrebat or not foloseste_llm:
        return rezultat

    if not cheie_api:
        logging.info(
            f"ℹ️ [Classifier] {len(de_intrebat)} produse neclare local, fara cheie "
            "Gemini pentru rafinare — trec pe fluxul clasic."
        )
        return rezultat

    relevante_nisa = set(tipuri_relevante(nisa))

    for start in range(0, len(de_intrebat), MAX_NUME_PER_APEL):
        lot = de_intrebat[start:start + MAX_NUME_PER_APEL]
        inceput = time.time()
        try:
            verdicte = _apeleaza_gemini(lot, nisa, cheie_api)
        except Exception as e:
            logging.warning(f"⚠️ [Classifier] Apelul Gemini a esuat: {e}")
            continue

        primite = {}
        for v in verdicte:
            try:
                nume = lot[int(v.get("index", -1))]
            except (ValueError, IndexError, TypeError):
                continue
            tip = _normalizeaza(v.get("tip", ""))
            linie = _normalizeaza(v.get("linie", ""))
            set_ = _normalizeaza(v.get("set", ""))
            # Relevanta se recalculeaza din regulile nisei — modelul nu poate
            # inventa ca ceva e relevant.
            primite[nume] = {
                "linie": linie,
                "set": set_,
                "tip": tip,
                "id_canonic": construieste_id(linie, set_, tip, nume),
                "relevant": tip in relevante_nisa,
                "sursa": "gemini",
            }

        with _lock:
            cache = _incarca_cache()
            for nume, verdict in primite.items():
                rezultat[nume] = verdict
                if verdict["tip"] not in ("", "necunoscut"):
                    cache[_cheie(nume, nisa)] = verdict
            _salveaza_cache()

        numar_relevante = sum(1 for n in lot if rezultat[n].get("relevant"))
        logging.info(
            f"🧠 [Classifier] {len(lot)} produse neclare trimise la Gemini, "
            f"{time.time() - inceput:.1f}s ({numar_relevante} relevante)"
        )

    return rezultat
