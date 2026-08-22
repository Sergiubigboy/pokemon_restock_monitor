import json
import random
import sys
import time
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from modules.scraper import check_search_page_stock
from modules.notifier import (send_telegram_notification, send_watchlist_alert,
                             send_set_alert)
from modules.watchlist import (
    load_watchlist,
    match_item,
    evaluate,
    record_alert,
    watchlist_is_stale,
)
from modules.price_parser import parse_price_ron
from modules.item_stats import record_item_alert, record_item_reject, flush as flush_item_stats
from modules import beta, classifier, feedback, policy, price_check
from modules.state_manager import (
    load_known_products,
    save_known_products,
    add_product,
    remove_stale_products,
    poate_notifica,
    marcheaza_notificat,
)
from modules.bot_controller import start_bot_thread, monitor_state, alert_site_failure

# ─────────────────────────────────────────────────────────────────
#  CONFIGURARE LOGGING
# ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# ─────────────────────────────────────────────────────────────────
#  CONFIGURARE
# ─────────────────────────────────────────────────────────────────
TURBO_INTERVAL = 1   # secunde interval turbo — fix

# ─────────────────────────────────────────────────────────────────
#  Loader config
# ─────────────────────────────────────────────────────────────────
def load_json_list(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

# ─────────────────────────────────────────────────────────────────
#  VIP matching
# ─────────────────────────────────────────────────────────────────
def match_vip(product_name_lower: str, vip_groups: list) -> tuple[bool, str | None]:
    for group in vip_groups:
        keywords = group.get("keywords", [])
        message  = group.get("message", None)
        if any(kw.lower() in product_name_lower for kw in keywords if kw.strip()):
            return True, message
    return False, None

# ─────────────────────────────────────────────────────────────────
#  Grupare pe nișe
# ─────────────────────────────────────────────────────────────────
def grupeaza_pe_nise(sites: list) -> dict:
    """
    Grupează magazinele după câmpul "niche" din sites_config.json.

    Site-urile fără "niche" ajung într-un grup comun, deci configurațiile
    vechi continuă să funcționeze fără nicio modificare (o singură nișă =
    exact comportamentul secvențial de dinainte).
    """
    grupuri = {}
    for site in sites:
        nisa = site.get("niche") or "General"
        grupuri.setdefault(nisa, []).append(site)
    return grupuri


# ─────────────────────────────────────────────────────────────────
#  Brief de pret (mesaj separat, dupa alerta)
# ─────────────────────────────────────────────────────────────────
def trimite_brief(text: str):
    """Trimite brief-ul de pret doar catre admin, fara sa incetineasca alerta."""
    from modules.bot_controller import _send_message, ADMIN_ID
    if ADMIN_ID:
        _send_message(ADMIN_ID, text)


# ─────────────────────────────────────────────────────────────────
#  Scanarea unui singur magazin
# ─────────────────────────────────────────────────────────────────
def scaneaza_site(site: dict, known_products: dict, vip_groups: list,
                  blacklist_keywords: list, watchlist_data: dict, watchlist_activ: bool):
    nisa = site.get("niche") or "General"
    """Logica per magazin — identică cu cea de dinainte, doar extrasă în funcție."""
    site_name = site["name"]

    # ── Sărim site-urile cu mute activ ──
    if monitor_state.is_muted(site_name):
        logging.info(f"🔇 [{site_name}] MUTED — sărit.")
        return

    found_products = check_search_page_stock(site)

    # ── Inităm site-ul dacă e prima dată când apare ──
    if site_name not in known_products:
        known_products[site_name] = set()

    # ── Pre-filtrare: ce produse ar genera notificare ─────────
    # Clasificarea LLM se aplică DOAR pe astea, nu pe tot stocul. Practic
    # doar produsele noi ajung la Gemini, adică sub 10 nume pe zi — restul
    # sunt deja în cache-ul permanent și nu costă nimic.
    debug_activ = monitor_state.debug_mode or monitor_state.debug_mode_all
    candidati = []
    for p in found_products:
        nume_mic = p["name"].strip().lower()
        e_vip, _ = match_vip(nume_mic, vip_groups)
        e_negru = any(b.lower() in nume_mic for b in blacklist_keywords if b.strip())
        if e_negru and not e_vip:
            continue
        if debug_activ or nume_mic not in known_products[site_name]:
            candidati.append(p["name"])

    clasificari = {}
    if candidati and monitor_state.is_classifier_enabled():
        clasificari = classifier.clasifica(candidati, nisa)

    # ── Procesăm produsele găsite ─────────────────────
    valid_count       = 0
    current_valid_names = set()

    for p in found_products:
        p_name       = p["name"]
        p_name_lower = p_name.strip().lower()
        p_url        = p["url"]
        p_img        = p["image"]
        p_price      = p["price"]

        is_vip, vip_message = match_vip(p_name_lower, vip_groups)
        is_black = any(b.lower() in p_name_lower for b in blacklist_keywords if b.strip())

        if is_black and not is_vip:
            continue

        valid_count += 1
        current_valid_names.add(p_name_lower)

        # Trimite notificare dacă e produs NOU (sau DEBUG)
        debug_trigger = monitor_state.debug_mode or monitor_state.debug_mode_all
        e_nou = p_name_lower not in known_products[site_name]

        # Plasa de siguranță împotriva duplicatelor: chiar dacă produsul apare
        # ca "nou" (stare pierdută, JSON șters de un git pull), nu îl trimitem
        # din nou dacă a plecat deja o notificare pentru el recent.
        if e_nou and not debug_trigger and not poate_notifica(site_name, p_name_lower):
            logging.info(f"🔁 [{site_name}] Renotificare evitată (trimis recent): {p_name}")
            add_product(known_products, site_name, p_name_lower)
            continue

        if debug_trigger or e_nou:

            # ── Verdictul clasificatorului ────────────────────
            verdict = clasificari.get(p_name) or {}
            id_canonic = verdict.get("id_canonic", "")
            relevant = verdict.get("relevant")

            # "Bad" apăsat pe Telegram blochează produsul în TOATE magazinele,
            # pentru că lista e ținută pe id canonic, nu pe numele brut.
            if id_canonic and feedback.este_respins(id_canonic):
                logging.info(f"⛔ [{site_name}] Blocat de tine ({id_canonic}): {p_name}")
                if not debug_trigger:
                    add_product(known_products, site_name, p_name_lower)
                continue

            # Politica de nișă + inteligența de set decid ce se întâmplă.
            # Un set bun (tier S) declanșează pe TOATE tipurile urmărite —
            # ETB, booster box, UPC — nu alegi produs cu produs.
            decizie_politica = policy.decide(verdict, nisa) if verdict else None
            motiv_filtrare = ""

            # In DEBUG vrei sa vezi TOT, inclusiv ce ar fi fost filtrat —
            # altfel comanda /debug nu-ti arata nimic, ca filtrul taie inainte
            # sa apuce sa trimita. Motivul filtrarii merge in mesaj, ca sa poti
            # judeca daca regula e corecta.
            if decizie_politica is not None and decizie_politica.actiune == "TACERE":
                logging.info(f"🔕 [{site_name}] {decizie_politica.motiv}: {p_name}")
                if not debug_trigger:
                    add_product(known_products, site_name, p_name_lower)
                    continue
                motiv_filtrare = decizie_politica.motiv

            if monitor_state.debug_mode_all and (p_name_lower in known_products[site_name]):
                debug_target = 'all'
            elif monitor_state.debug_mode and (p_name_lower in known_products[site_name]):
                debug_target = 'admin'
            else:
                debug_target = None

            delay_seconds = monitor_state.delay_seconds if monitor_state.delay_mode else 0

            # ── Evaluare pe watchlist (aditivă) ───────────────
            # Dacă produsul e pe watchlist și trece toate pragurile,
            # primește alerta bogată cu profit net și ROI, care o
            # înlocuiește pe cea clasică (conține strict mai multă
            # informație). În orice alt caz pleacă notificarea
            # obișnuită — nu se pierde niciun produs nou în stoc.
            decizie = None
            if watchlist_activ:
                item = match_item(p_name, site_name, watchlist_data)
                if item:
                    decizie = evaluate(p, item, watchlist_data)

            if decizie is not None and decizie.should_alert:
                logging.info(
                    f"💰 [OPORTUNITATE] {site_name} -> {p_name} ({p_price}) | "
                    f"{decizie.label} [{decizie.tier}] | "
                    f"net {decizie.net_profit_ron:+.0f} RON · ROI {decizie.roi_pct * 100:+.0f}%"
                )
                send_watchlist_alert(
                    decizie, p_name, p_url, site_name,
                    image_url=p_img,
                    stock_qty=p.get("qty"),
                    debug_target=debug_target,
                    delay_seconds=delay_seconds,
                    id_canonic=id_canonic
                )
                if not debug_trigger:
                    record_alert(decizie.item_id)
                    record_item_alert(decizie.item_id, site_name, decizie.net_profit_ron)
            else:
                if decizie is not None:
                    logging.info(
                        f"🔎 [Watchlist] {p_name} → {decizie.label} "
                        f"[{decizie.tier}] RESPINS: {decizie.reason}"
                    )
                    record_item_reject(decizie.item_id, decizie.reason)

                # Set cercetat, tier S/A -> alerta cu decizia deja luata.
                # Restul merge pe notificarea clasica, neschimbata.
                if decizie_politica is not None and decizie_politica.e_urgent:
                    logging.info(
                        f"🔥 [{decizie_politica.tier_set}] {site_name} -> {p_name} "
                        f"({p_price}) | {decizie_politica.motiv}"
                    )
                    send_set_alert(
                        decizie_politica, p_name, p_url, site_name, p_price,
                        image_url=p_img, stock_qty=p.get("qty"),
                        debug_target=debug_target, delay_seconds=delay_seconds,
                        id_canonic=id_canonic
                    )
                    # Brief-ul de pret pleaca DUPA alerta, ca mesaj separat.
                    # Alerta nu are voie sa astepte dupa nimic — produsul bun
                    # intra o singura data. Citim doar din registru, fara retea.
                    try:
                        text_brief = price_check.brief(
                            id_canonic, parse_price_ron(p_price))
                        if text_brief:
                            trimite_brief(text_brief)
                    except Exception as e:
                        logging.warning(f"⚠️ Brief de pret esuat: {e}")
                else:
                    if decizie_politica is not None:
                        status = "👀 [SEMNAL]"
                    else:
                        status = "💎 [VIP]" if is_vip else "✨ [NOU]"
                    logging.info(f"{status} {site_name} -> {p_name} ({p_price})")

                    mesaj_vip = vip_message
                    if motiv_filtrare:
                        # In debug: spune de ce ar fi fost filtrat in mod normal.
                        mesaj_vip = f"🔕 FILTRAT NORMAL: {motiv_filtrare}"
                        is_vip = True

                    send_telegram_notification(
                        p_name, p_url, p_price, site_name,
                        p_img, is_vip, mesaj_vip,
                        debug_target=debug_target,
                        delay_seconds=delay_seconds,
                        id_canonic=id_canonic
                    )

            if not debug_trigger:
                add_product(known_products, site_name, p_name_lower)
                marcheaza_notificat(site_name, p_name_lower)

            time.sleep(1.5)

    # ── Eliminăm produsele dispărute din JSON ─────────────────
    if found_products:
        # A doua protecție anti-duplicat: dacă magazinul a întors brusc mult
        # mai puține produse decât la scanarea precedentă, e aproape sigur o
        # randare parțială, nu un magazin care s-a golit. Curățarea stării în
        # momentul ăla ar renotifica tot ce lipsește, la ciclul următor.
        produse_anterior = monitor_state.get_site_product_count(site_name)
        scanare_partiala = produse_anterior > 0 and valid_count < produse_anterior * 0.5

        if scanare_partiala:
            logging.warning(
                f"⚠️ [{site_name}] Scanare probabil PARȚIALĂ: {valid_count} produse "
                f"față de {produse_anterior} anterior. Nu curăț starea."
            )
        else:
            remove_stale_products(known_products, site_name, current_valid_names)

        monitor_state.record_site_ok(site_name, valid_count)
    else:
        consec = monitor_state.record_site_fail(site_name)
        monitor_state.record_error(f"{site_name}: scraper returnat 0 produse", site_name)
        logging.warning(f"⚠️ [{site_name}] Scraper a returnat 0 produse — JSON păstrat neschimbat. (eșec #{consec})")
        alert_site_failure(site_name, consec)

    logging.info(f"📊 [{site_name}] Scanare completă: {valid_count} produse valide.")


# ─────────────────────────────────────────────────────────────────
#  Scanarea unei nișe — magazinele ei, STRICT pe rând
# ─────────────────────────────────────────────────────────────────
def scaneaza_nisa(nume_nisa: str, site_uri: list, known_products: dict, vip_groups: list,
                  blacklist_keywords: list, watchlist_data: dict, watchlist_activ: bool):
    """
    Paralelismul e DOAR între nișe. În interiorul unei nișe magazinele se
    scanează unul după altul, exact ca înainte — asta a fost mereu regula
    care ține botul nedetectat.
    """
    for site in site_uri:
        if monitor_state.paused:
            break
        try:
            scaneaza_site(site, known_products, vip_groups, blacklist_keywords,
                          watchlist_data, watchlist_activ)
        except Exception as e:
            # Un magazin căzut nu are voie să oprească restul nișei.
            nume_site = site.get("name", "?")
            logging.error(f"❌ [{nume_nisa}/{nume_site}] Eroare la scanare: {e}", exc_info=True)
            monitor_state.record_error(f"{nume_site}: {e}", nume_site)


# ─────────────────────────────────────────────────────────────────
#  Main loop
# ─────────────────────────────────────────────────────────────────
def main():
    sleep_time = random.uniform(2.1, 4.5)
    time.sleep(sleep_time)
    # --- Argumente CLI ---
    parser = argparse.ArgumentParser(description="Pokemon Restock Monitor")
    parser.add_argument("--turbo", action="store_true", help="Porneste in Turbo Mode (interval 1s)")
    args = parser.parse_args()

    if args.turbo:
        monitor_state.set_turbo(True)
        logging.info("⚡ Pornit în TURBO MODE!")

    logging.info("\n" + "="*54)
    logging.info("  🔥 POKEMON STOCK MONITOR V4.0 - TURBO & BOT CONTROL 🔥")
    logging.info("="*54 + "\n")

    # --- Pornire bot Telegram în fundal ---
    start_bot_thread()

    # --- Încărcăm starea persistentă de pe disk ---
    initial = load_known_products()
    if initial:
        total = sum(len(v) for v in initial.values())
        logging.info(f"📂 Context încărcat din JSON: {total} produse cunoscute din {len(initial)} magazine.\n")
    else:
        logging.info("📂 Nu există context anterior. Prima rulare — toate produsele găsite vor fi notificate.\n")

    # Ține minte dacă am anunțat deja că watchlist-ul e expirat, ca să nu
    # repetăm avertismentul la fiecare ciclu (în turbo ar fi la 1 secundă).
    watchlist_expirat_anuntat = False

    # ─────────────────────────────────────────────────────────────
    #  Loop principal
    # ─────────────────────────────────────────────────────────────
    while True:
        try:
            # ── Verificare pauză ──────────────────────────────────
            if monitor_state.paused:
                logging.info("⏸ [PAUZĂ] Monitorul e pe pauză. Aștept...")
                time.sleep(5)
                continue

            # ── REÎNCĂRCĂM CONFIGURILE LA FIECARE CICLU ──
            known_products = load_known_products()
            sites              = load_json_list("config/sites_config.json")
            vip_groups         = load_json_list("config/vip_keywords.json")
            blacklist_keywords = load_json_list("config/blacklist_keywords.json")
            # Watchlist-ul beneficiază de același hot-reload. Dacă fișierul
            # lipsește sau e JSON invalid, load_watchlist() întoarce {} și
            # logează o singură dată — monitorul continuă pe fluxul clasic.
            watchlist_data     = load_watchlist()
            # beta.json e recitit la fiecare ciclu, deci comuti fara restart
            beta.reseteaza()

            # Evaluarea rulează doar dacă e pornită din /watchlist ȘI avem date.
            watchlist_activ = monitor_state.is_watchlist_enabled() and bool(watchlist_data)

            # Avertizăm o singură dată dacă agentul săptămânal nu a mai rulat.
            if watchlist_data:
                expirat = watchlist_is_stale(watchlist_data)
                if expirat and not watchlist_expirat_anuntat:
                    logging.warning(
                        "⚠️ [Watchlist] Fișierul e EXPIRAT (_meta.valid_until a trecut). "
                        "Prețurile de revânzare nu mai sunt de încredere — rulează agentul săptămânal."
                    )
                    monitor_state.record_error("Watchlist expirat — agentul săptămânal nu a mai rulat")
                watchlist_expirat_anuntat = expirat

            scan_start = time.time()

            # ── Scanăm nișele în paralel, site-urile din fiecare pe rând ──
            grupuri = grupeaza_pe_nise(sites)
            paralel = max(1, min(monitor_state.get_parallel_niches(), len(grupuri)))

            if paralel == 1:
                for nume_nisa, site_uri in grupuri.items():
                    scaneaza_nisa(nume_nisa, site_uri, known_products, vip_groups,
                                  blacklist_keywords, watchlist_data, watchlist_activ)
            else:
                logging.info(f"🧵 Scanez {len(grupuri)} nișe, {paralel} în paralel.")
                with ThreadPoolExecutor(max_workers=paralel, thread_name_prefix="nisa") as executor:
                    sarcini = [
                        executor.submit(scaneaza_nisa, nume_nisa, site_uri, known_products,
                                        vip_groups, blacklist_keywords, watchlist_data, watchlist_activ)
                        for nume_nisa, site_uri in grupuri.items()
                    ]
                    for sarcina in as_completed(sarcini):
                        # Excepțiile sunt deja prinse în scaneaza_nisa; asta e
                        # doar plasa de siguranță pentru ce ar scăpa.
                        try:
                            sarcina.result()
                        except Exception as e:
                            logging.error(f"❌ Nișă eșuată complet: {e}", exc_info=True)

            # ── Actualizăm statistici bot ──────────────────────────
            monitor_state.record_scan()
            # Golim tamponul de statistici pe item o dată per ciclu.
            flush_item_stats()
            scan_elapsed = time.time() - scan_start

            # ── Interval de aşteptare (normal sau turbo) ───────────────
            if monitor_state.turbo_mode:
                interval = TURBO_INTERVAL
                logging.info(f"⚡ [TURBO] Scanare completă în {scan_elapsed:.1f}s. Reiau în {interval}s...")
            else:
                interval = monitor_state.check_interval
                logging.info(f"⏳ Pauză {interval}s ({interval//60}m {interval%60}s)...")

            time.sleep(interval)

        except KeyboardInterrupt:
            logging.info("🛑 Monitor oprit manual. La revedere!")
            break
        except Exception as e:
            err_msg = str(e)
            logging.critical(f"❌ Eroare critică în bucla principală: {err_msg}", exc_info=True)
            monitor_state.record_error(f"CRITIC: {err_msg}")
            time.sleep(60)

if __name__ == "__main__":
    main()