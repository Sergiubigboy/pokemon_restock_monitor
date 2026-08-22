# Prompt pentru Claude Code — Etapa 1

Copiază tot ce e între linii în Claude Code, deschis în folderul `pokemon_restock_monitor`.

---

Lucrez la un monitor de restock pentru scalping retail (Pokémon TCG și alte nișe), care rulează
pe un Raspberry Pi 5 cu 4GB. Codul există și funcționează. Vreau să-l extind, nu să-l rescriu.

## Ce face proiectul acum

`main.py` rulează o buclă infinită. La fiecare ciclu:
1. Reîncarcă `config/sites_config.json`, `config/vip_keywords.json`, `config/blacklist_keywords.json`
   (main.py, liniile 97-100) — **configurația e hot-reload, nu necesită restart**
2. Pentru fiecare magazin din `sites_config.json`, apelează `check_search_page_stock(site)` din
   `modules/scraper.py`
3. Filtrează produsele: sare peste cele din blacklist (dacă nu sunt VIP)
4. Pentru fiecare produs nou (care nu e în `known_products.json`), trimite notificare Telegram prin
   `modules/notifier.py`
5. Marchează produsele dispărute și salvează starea

Detalii importante despre cod:

- `modules/scraper.py` folosește Playwright cu `launch_persistent_context`, `channel="msedge"`,
  profil de browser persistent per magazin (`config/profiles/<nume>_profile`) și
  `--disable-blink-features=AutomationControlled`. **Aceasta e soluția anti-bot și funcționează.
  NU o schimba, nu o „optimiza", nu înlocui Playwright cu requests pentru site-urile existente
  decât dacă îți cer explicit.**
- `check_search_page_stock()` întoarce o listă de dict-uri:
  `{"name": str, "url": str, "image": str|None, "price": str}`
  unde `price` e **text brut** din pagină, în formate variate: `"1.017,00 lei"`, `"599,99 RON"`,
  `"Preț: 289 lei"`, `"N/A"`.
- `modules/notifier.py` → `send_telegram_notification(product_name, product_url, product_price,
  site_name, image_url, is_vip, vip_message, debug_target, delay_seconds)`. Trimite către admin,
  VIP-uri și un canal, cu suport de întârziere pentru canal.
- `modules/state_manager.py` gestionează `known_products.json` și istoricul apariții/dispariții.
- `modules/bot_controller.py` expune `monitor_state` (turbo, paused, debug, check_interval, mute
  per site) și comenzi Telegram: `/turbo /normal /pause /resume /debug /interval /mute /stats` etc.
- Sunt 8 magazine în `sites_config.json`: Krit, Redgoblin, LexShop, Noriel, PokeMANIA, Bookcity,
  SMYK, Bebe Tei.

## Ce am adăugat deja (fișiere noi, încă neintegrate în cod)

`config/watchlist.json` și `config/calendar.json` există deja în repo, cu date reale. Citește-le
înainte de a scrie cod — structura lor e contractul pe care trebuie să-l implementezi.

`watchlist.json` conține `defaults` (comision platformă, cost livrare, praguri minime, `stale_after_days`)
și o listă `items`. Fiecare item are:
- `id`, `enabled`, `tier` (S/A/B/C), `niche`, `label`
- `match`: `include_all[]`, `include_any[]`, `exclude[]` — reguli de potrivire pe numele produsului
- `buy`: `max_price_ron`, `sites[]` (nume exacte din sites_config), `max_qty_per_drop`
- `resale`: `median_ron`, `checked_at` (dată ISO), `liquidity_30d`, `confidence`
- `thresholds`: `min_profit_ron`, `min_roi_pct`
- opțional `shipping_cost_ron` (suprascrie default-ul)

Aceste fișiere sunt rescrise săptămânal de un agent separat. Codul tău doar le citește.
**Botul nu are voie să scrie în ele.**

## Ce vreau să construiești acum (Etapa 1)

### 1. `modules/price_parser.py`

O funcție `parse_price_ron(text: str) -> float | None` care transformă textul brut de preț în număr.

Trebuie să gestioneze formatul românesc, unde **punctul e separator de mii și virgula e zecimală** —
exact invers față de convenția engleză. Cazuri obligatorii de acoperit:

```
"1.017,00 lei"  -> 1017.0
"599,99 RON"    -> 599.99
"Preț: 289 lei" -> 289.0
"289"           -> 289.0
"1 017,00 lei"  -> 1017.0     (spațiu ca separator de mii)
"29,99"         -> 29.99
"N/A"           -> None
""              -> None
"de la 199 lei" -> 199.0
```

Atenție la ambiguitate: `"1.017"` în context românesc e o mie șaptesprezece, nu 1.017. Regula
practică: dacă după ultimul punct urmează exact 3 cifre și nu mai e nicio virgulă, punctul e
separator de mii.

Scrie și `tests/test_price_parser.py` cu toate cazurile de mai sus plus edge case-uri pe care le
identifici tu. Rulează testele și arată-mi că trec.

### 2. `modules/watchlist.py`

Modulul care încarcă watchlist-ul și decide dacă un produs merită alertă.

```python
load_watchlist(path="config/watchlist.json") -> dict
match_item(product_name: str, site_name: str, watchlist: dict) -> item | None
evaluate(product: dict, item: dict, watchlist: dict) -> Decision
```

`match_item` verifică, case-insensitive, pe numele produsului normalizat (lowercase, fără diacritice):
toate cuvintele din `include_all` trebuie să apară, cel puțin unul din `include_any` (dacă lista nu e
goală), și niciunul din `exclude`. În plus, `site_name` trebuie să fie în `item["buy"]["sites"]`.
Dacă mai multe item-uri se potrivesc, câștigă cel cu tier-ul cel mai înalt (S > A > B > C).

`evaluate` calculează:

```
net = resale.median_ron * (1 - platform_fee_pct) - shipping_cost_ron - pret_observat
roi = net / pret_observat
```

și întoarce un obiect cu: `should_alert: bool`, `net_profit_ron`, `roi_pct`, `reason: str`
(motivul respingerii, când e cazul), plus datele necesare pentru mesaj.

`should_alert` e True doar dacă TOATE condițiile sunt îndeplinite:
- `net >= thresholds.min_profit_ron`
- `roi >= thresholds.min_roi_pct`
- `pret_observat <= buy.max_price_ron`
- `resale.liquidity_30d >= defaults.min_liquidity_30d`
- `(azi - resale.checked_at).days <= defaults.stale_after_days`
- item-ul are `enabled: true`
- item-ul nu are `expires_at` în trecut
- numărul de alerte trimise azi pentru acest `id` < `defaults.max_alerts_per_item_per_day`

Ultima condiție are nevoie de un contor persistent — pune-l într-un fișier separat
`config/alert_counts.json`, resetat zilnic. **Adaugă-l în `.gitignore`** (vezi punctul 4).

Dacă `parse_price_ron` întoarce `None` (preț nedetectabil), NU trimite alertă de cumpărare —
loghează-l ca avertisment, pentru că înseamnă că selectorul de preț s-a stricat.

Scrie și teste pentru `match_item` și `evaluate`, inclusiv cazuri negative (preț peste plafon,
lichiditate insuficientă, date expirate).

### 3. Integrarea în `main.py`

Modifică bucla astfel încât, pentru fiecare produs găsit:

1. Încearcă întâi potrivirea pe watchlist (`match_item`)
2. Dacă se potrivește → `evaluate` → alertă **numai** dacă `should_alert` e True. Alerta trebuie
   să conțină profitul net și ROI-ul, nu doar „e în stoc".
3. Dacă NU se potrivește pe watchlist → păstrează comportamentul actual (VIP keywords + blacklist +
   notificare de produs nou). **Nu rupe fluxul existent** — vreau să pot rula sistemul nou și cel
   vechi în paralel până am încredere în praguri.

Adaugă un flag în `monitor_state` (și o comandă Telegram `/watchlist on|off`) care pornește sau
oprește evaluarea pe watchlist, ca să pot testa fără să repornesc procesul.

Încarcă `watchlist.json` în același loc unde se încarcă celelalte configurații (main.py ~linia 97),
ca să beneficieze de hot-reload. Dacă fișierul lipsește sau e JSON invalid, loghează eroarea și
continuă cu comportamentul vechi — **nu opri botul**.

### 4. Reparație `.gitignore`

Fișierele de stare sunt scrise de bot de zeci de ori pe oră. Dacă rămân urmărite de Git,
`git pull` pe Pi va eșua permanent cu „local changes would be overwritten". Adaugă:

```
config/known_products.json
config/historical_products.json
config/state.json
config/muted_sites.json
config/alert_counts.json
*.log
```

Verifică dacă sunt deja în index (`git ls-files config/`) și, dacă da, scoate-le cu
`git rm --cached <fișier>` fără să le ștergi de pe disk. Arată-mi ce comenzi rulezi înainte să le rulezi.

### 5. Formatul alertei

Extinde `modules/notifier.py` cu o funcție nouă pentru alertele de tip watchlist (nu o modifica pe
cea existentă). Formatul dorit:

```
🚨 OPORTUNITATE LIVE · Pokémon TCG

30th Celebration — Elite Trainer Box
Krit.ro · 6 buc în stoc

Achiziție      289 RON  (plafon 325)
Revânzare      520 RON  (cardmarket+vinted-ro)
Profit net     +159 RON  ·  +55% ROI
Lichiditate    22 vânzări / 30 zile
Preț verificat acum 2 zile · încredere medie

Max recomandat: 4 buc → +636 RON

[🛒 Cumpără]  [✖️ Ignoră 24h]
```

Butoanele inline pot rămâne pentru mai târziu dacă adaugă complexitate — spune-mi dacă preferi
să le lași pe Etapa 4. Deocamdată e suficient linkul către produs.

## Constrângeri

- **Python 3.11+**, dependențele din `requirements.txt`. Dacă adaugi o bibliotecă nouă, justific-o
  întâi — rulează pe Pi cu ARM și RAM limitat.
- Codul rulează 24/7 pe un Pi cu 4GB. Nu introduce nimic care ține date mari în memorie.
- Nu atinge `modules/scraper.py`, `config/sites_config.json`, `config/profiles/` sau `config/.env`.
- Nu schimba logica anti-bot din Playwright.
- Comentariile și logurile sunt în română, ca restul proiectului.
- Mesajele de log existente folosesc emoji ca prefix vizual — păstrează convenția.

## Cum vreau să lucrezi

Începe prin a citi `main.py`, `modules/scraper.py`, `modules/notifier.py`,
`modules/state_manager.py`, `config/watchlist.json` și `config/calendar.json`. Apoi
**arată-mi planul înainte să scrii cod** — vreau să văd ce fișiere creezi, ce modifici și în ce
ordine, ca să pot corecta direcția din start.

După implementare, arată-mi cum testez local, pe Windows, fără să pornesc scraperul real —
ideal cu un script care alimentează motorul de decizie cu produse false și îmi arată ce alerte
ar fi ieșit.
