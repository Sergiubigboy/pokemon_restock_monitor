import json
import sys
import time
import argparse
import logging
from modules.scraper import check_search_page_stock
from modules.notifier import send_telegram_notification
from modules.state_manager import (
    load_known_products,
    save_known_products,
    add_product,
    remove_stale_products,
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
#  Main loop
# ─────────────────────────────────────────────────────────────────
def main():
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

            scan_start = time.time()

            for site in sites:
                if monitor_state.paused:
                    break

                site_name = site["name"]

                # ── Sărim site-urile cu mute activ ──
                if monitor_state.is_muted(site_name):
                    logging.info(f"🔇 [{site_name}] MUTED — sărit.")
                    continue

                found_products = check_search_page_stock(site)

                # ── Inităm site-ul dacă e prima dată când apare ──
                if site_name not in known_products:
                    known_products[site_name] = set()

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
                    if monitor_state.debug_mode or (p_name_lower not in known_products[site_name]):
                        status = "💎 [VIP]" if is_vip else "✨ [NOU]"
                        logging.info(f"{status} {site_name} -> {p_name} ({p_price})")

                        send_telegram_notification(
                            p_name, p_url, p_price, site_name,
                            p_img, is_vip, vip_message
                        )

                        if not monitor_state.debug_mode:
                            add_product(known_products, site_name, p_name_lower)

                        time.sleep(1.5)

                # ── Eliminăm produsele dispărute din JSON ─────────────────
                if found_products:
                    remove_stale_products(known_products, site_name, current_valid_names)
                    monitor_state.record_site_ok(site_name, valid_count)
                else:
                    consec = monitor_state.record_site_fail(site_name)
                    monitor_state.record_error(f"{site_name}: scraper returnat 0 produse", site_name)
                    logging.warning(f"⚠️ [{site_name}] Scraper a returnat 0 produse — JSON păstrat neschimbat. (eșec #{consec})")
                    alert_site_failure(site_name, consec)

                logging.info(f"📊 [{site_name}] Scanare completă: {valid_count} produse valide.")

            # ── Actualizăm statistici bot ──────────────────────────
            monitor_state.record_scan()
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