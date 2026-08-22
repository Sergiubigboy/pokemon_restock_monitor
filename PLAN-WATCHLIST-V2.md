# Plan — Watchlist V2: sistem care știe dinainte, pe orice nișă

## 0. Constrângerea care dictează totul

> **Un produs bun intră o singură dată. Nu e timp de gândit.**

Asta invalidează orice sistem care învață din repetiție. Ce se repetă des e, prin
definiție, ce nu e rar — adică exact ce nu-ți aduce bani. Momentul în care ai
nevoie de decizie e primul și singurul în care produsul apare.

Deci sistemul nu are voie să *evalueze* la momentul drop-ului. Trebuie să aibă
decizia **deja luată**, iar la drop să spună doar „acum, ăsta, atât".

Toată arhitectura de mai jos servește obiectivul ăsta.

---

## 1. Diagnosticul: un fișier face trei treburi diferite

`watchlist.json` amestecă azi trei lucruri cu ritmuri și surse complet diferite:

| Ce | Se schimbă | Sursă | Scalează? |
|---|---|---|---|
| **Politică** — ce categorii vreau | lunar | tu | da, trivial |
| **Identitate** — ce e produsul ăsta | niciodată | automat, deja rezolvat | da |
| **Inteligență de set** — ce merită | săptămânal | research web | **da, dacă e factual** |
| **Preț de piață** — cât face | săptămânal | piața | greu |

Fiindcă sunt în același fișier, tot ansamblul merge la viteza celui mai manual.
Ca să adaugi o nișă azi scrii `include_all`, `include_any`, `exclude`,
`max_price_ron`, `median_ron` — pentru fiecare produs. Sute de câmpuri manuale.

---

## 2. Cele patru straturi

### 2.1 Politica — `config/niche_policy.json`

Ce **tipuri** de produs contează, per nișă. Clasificatorul știe deja să spună
tipul, deci dispar toate regulile de potrivire pe nume.

```json
{
  "Pokemon TCG": {
    "tipuri_urmarite": ["booster_box", "etb", "upc", "booster_bundle"],
    "tipuri_ignorate": ["blister", "tin", "battle_deck", "single_card", "accesoriu"],
    "capital_max_ron": 5000
  }
}
```

**O nișă nouă = 5 rânduri.**

### 2.2 Identitatea — deja funcțională

`modules/classifier.py` produce `linie|set|tip` offline, gratuit. Verificat pe
stocul real: 16 din 17 nume clasificate local, 1 trimis la Gemini. Id-ul
colapsează între magazine — Krit și Bookcity dau același
`pokemon|pitch-black|booster-pack` din nume complet diferite.

### 2.3 Inteligența de set — `config/set_intelligence.json` ← **piesa nouă**

Aici stă răspunsul la „ce merită", **aflat înainte de drop**. Două categorii,
exact cum le-ai descris:

```json
{
  "Pokemon TCG": {
    "30th celebration": {
      "tier": "S",
      "categorie": "lansare_viitoare",
      "lanseaza_la": "2026-09-16",
      "motiv": "aniversar 30 ani, lansare globala simultana, peste 150 carti",
      "surse": ["https://www.pokemon.com/...", "https://www.dexerto.com/..."],
      "verificat_la": "2026-08-19",
      "actiune": "orice produs din set, la orice pret sub retail+20%"
    },
    "prismatic evolutions": {
      "tier": "S",
      "categorie": "istoric_dovedit",
      "motiv": "epuizat la lansare, probleme de aprovizionare recunoscute oficial",
      "surse": ["https://bulbagarden.net/...", "https://www.ign.com/..."],
      "verificat_la": "2026-08-19",
      "actiune": "cumpara la orice restock"
    },
    "ascended heroes": { "tier": "A", "categorie": "istoric_dovedit", "...": "..." }
  }
}
```

**Fără `surse` neverificabile nu intră nimic în fișier.** Vezi secțiunea 3.

### 2.4 Prețul — separat, și nu blochează nimic

`config/price_book.json`, cheie = id canonic, cu proveniență obligatorie.
Ai spus că găsești tu o soluție pentru second market — bine, structura e
pregătită, dar **nimic din restul sistemului nu așteaptă după ea**.

Alertele S/A funcționează fără preț: dacă un set e tier S, decizia e „cumpără",
nu „calculează". Prețul rafinează, nu autorizează.

---

## 3. Cum se face research-ul, fără halucinații

Am testat pe cheia ta, cu Gemini + `google_search`. Rezultatele sunt fără echivoc:

| Întrebare | Surse | Ce a ieșit |
|---|---|---|
| „când s-a lansat Prismatic Evolutions, s-a epuizat, cât face acum" | **12** | corect, bulbagarden + IGN |
| „ce produse are 30th Celebration, știri despre alocare" | **19** | corect, pokemon.com |
| „ce set să cumpăr ca să fac cei mai mulți bani" | **0** | vorbărie generică |
| „când se lansează Delta Reign" — **fără** căutare | 0 | **a negat că setul există** |

Ultima linie e cea importantă: **fără grounding, modelul neagă existența unui set
real.** Research-ul negrounded nu e doar inutil, e activ greșit.

### Regulile pipeline-ului de research

1. **Doar întrebări factuale.** „Când se lansează X", „s-a epuizat X la lansare",
   „există știri despre tiraj limitat la X". Niciodată „ce e cel mai bun".
2. **`google_search` obligatoriu** la fiecare apel.
3. **Zero surse = afirmația se aruncă.** Nu se scrie în fișier, nu ajunge în
   nicio alertă. Verificabil programatic: `len(groundingChunks) == 0` → discard.
4. **Sursele se salvează** în `set_intelligence.json`, ca să poți verifica oricând
   de unde vine un tier S.
5. **Tier-ul îl atribuie regula, nu modelul.** Modelul aduce fapte („s-a epuizat
   în 2 ore", „alocare EU redusă"); codul mapează faptele în tier. Modelul nu are
   voie să scrie direct „tier: S".

`tools/research_nisa.py --nisa "Pokemon TCG"` rulează asta săptămânal, pentru
orice nișă, cu același cod. Pentru o nișă nouă schimbi doar numele.

### Ce faci cu nișele pe care nu le știi

Ai zis că ai accepta să te întrebe. Compromisul: research-ul automat propune, tu
confirmi **o singură dată per set**, din Telegram:

```
🔬 Research nisa LEGO — 3 seturi propuse tier S
   10316 Rivendell — retragere anuntata 31 dec (brickfanatics.com)
   [✅ Accept]  [⛔ Respinge]  [🔍 Vezi sursele]
```

Pe Pokémon, unde te descurci singur, poți lăsa auto-accept. Pe nișe noi, un tap.

---

## 4. Alerte pe trei niveluri — asta rezolvă „intră orice mizerie"

Decizia se ia din combinația **tip relevant × tier de set**:

| Set | Tip | Ce primești |
|---|---|---|
| tier S/A în registru | relevant | 🔥 **CUMPĂRĂ ACUM** — decizie luată pe *dată*, cu motiv și surse |
| tier B/C sau necunoscut | relevant | 👀 descoperire, discret, fără urgență |
| orice | irelevant | tăcere totală |

Junk-ul care încă intră la Pokémon dispare pe două filtre: tipul (blister, tin,
battle deck — deja funcțional, testat) și setul necunoscut (canal discret).

Mesajul pentru tier S nu conține numere de evaluat:

```
🔥 CUMPĂRĂ — 30th Celebration ETB
Krit.ro · 289 lei · 6 buc

Decis pe 19 august: aniversar 30 ani, alocare EU sub cerere.
Plafon stabilit: 325 lei. Max: 4 buc.

[🛒 Cumpără]  [⛔ Nu mai trimite]
```

Zero calcule la ora drop-ului. Tot ce trebuie gândit a fost gândit acum trei
săptămâni.

---

## 5. Pre-poziționarea pentru drop-ul unic

`calendar.json` are deja `notify_offsets` și `promote_to_tier`. Se activează:

- **T-21d / -7d / -2d** — „pregătește capital, cont pre-logat pe Krit/Redgoblin"
- **T-2h** — magazinele nișei trec automat pe turbo
- **T-0** — alerta e deja pre-scrisă; nu se calculează nimic în momentul ăla
- **Preorder** — pentru seturile cu `preorder: true`, alerta pleacă la prima
  apariție a paginii, nu la stoc

Fereastra de preorder e adesea mai valoroasă decât drop-ul: se deschide cu 3-5
săptămâni înainte și nu e concurență de boți.

---

## 6. Scalarea la 100 de magazine

Fast-path-ul HTTP devine **obligatoriu**. Testat: toate 8 magazinele actuale
răspund fără browser, în 0,3-1,9s. Playwright rămâne excepția pentru anti-bot real.

Frecvența devine per magazin, calculată din `item_performance.json`:

| Nivel | Ce e acolo | Interval |
|---|---|---|
| fierbinte | drop activ în fereastră de calendar | 3-10 s |
| cald | produce alerte S/A regulat | 2-5 min |
| rece | restul | 15-30 min |

---

## 7. Ordinea de execuție

Fiecare fază lasă sistemul funcțional.

| # | Ce | De ce acum |
|---|---|---|
| 1 | `niche_policy.json` + `modules/policy.py` | deblochează adăugarea de nișe |
| 2 | `set_intelligence.json` + `tools/research_nisa.py` | **inima sistemului** — știe dinainte |
| 3 | Alerte pe 3 niveluri | filtrarea pe care o ceri acum |
| 4 | Pre-poziționare din calendar | prinde drop-ul unic |
| 5 | Confirmare din Telegram pentru nișe noi | încredere pe teren necunoscut |
| 6 | Frecvență adaptivă | abia peste ~20 magazine |
| 7 | `price_book.json` | când ai sursa ta de prețuri |

Faza 2 e cea care schimbă totul. Faza 7 nu blochează nimic.

---

## 8. Ce se schimbă pentru agentul săptămânal

**Nu mai face:** nu mai scrie item-uri individuale, nu mai calculează
`max_price_ron`, nu mai inventează `median_ron`, nu mai atribuie tier-uri direct.

**Face în schimb:** rulează `research_nisa.py` pe fiecare nișă activă, verifică
calendarul, raportează ce seturi au intrat/ieșit din registru și cu ce surse,
taie nișele care n-au produs nimic pe baza `item_performance.json`.

Regula nouă, explicită: **nicio afirmație fără sursă web verificabilă.**

---

## 9. Cum arată succesul

Ca să adaugi nișa Jellycat mâine:

1. `tools/generate_site_config.py --nisa Jellycat --save` × N magazine
2. 5 rânduri în `niche_policy.json`
3. `tools/research_nisa.py --nisa Jellycat` → propune seturile/modelele care
   contează, cu surse
4. Confirmi din Telegram ce accepți

Primești alerte 🔥 pentru ce e dovedit, 👀 pentru restul, tăcere pentru mizerie.
**Nimic scris de mână per produs**, și fiecare tier S are o sursă pe care o poți
deschide și citi.
