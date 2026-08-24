#!/usr/bin/env bash
# Verifica git. Daca s-a schimbat ceva, trage si reporneste botul.
# Daca nu s-a schimbat nimic, iese imediat si nu atinge botul.
set -uo pipefail

PROIECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROIECT" || exit 1
PYTHON="$PROIECT/venv/bin/python"

# Gasim singuri serviciul care ruleaza botul asta — nu presupunem numele.
gaseste_serviciu() {
  systemctl list-units --type=service --all --no-legend 2>/dev/null \
    | awk '{print $1}' | while read -r u; do
        if systemctl cat "$u" 2>/dev/null | grep -q "$PROIECT.*main.py"; then
          echo "$u"; return
        fi
      done
}
SERVICIU="$(gaseste_serviciu)"

git fetch origin --quiet 2>/dev/null || { echo "fetch esuat"; exit 0; }
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse @{u} 2>/dev/null)"
[ "$LOCAL" = "$REMOTE" ] && exit 0     # nimic nou, plecam

echo "=== $(date '+%F %T') cod nou pe git ==="

# Starea botului nu are voie sa se piarda la pull.
STARE=(config/known_products.json config/historical_products.json
       config/product_absence.json config/last_notified.json
       config/product_classifications.json config/rejected_products.json
       config/item_performance.json config/price_book.json
       config/alert_counts.json config/muted_sites.json config/beta.json
       config/sites_config.json)
rm -rf .backup_stare && mkdir -p .backup_stare/config
for f in "${STARE[@]}"; do [ -f "$f" ] && cp -p "$f" ".backup_stare/$f"; done

git diff --quiet || git stash push -m "auto-$(date +%s)" >/dev/null 2>&1
git pull --ff-only --quiet || { echo "pull esuat, las botul in pace"; exit 1; }

for f in "${STARE[@]}"; do
  [ -f ".backup_stare/$f" ] && { [ -f "$f" ] || cp -p ".backup_stare/$f" "$f"; }
done

git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -q requirements.txt \
  && "$PYTHON" -m pip install -q -r requirements.txt

# Configuratie stricata = nu repornim. Mai bine cod vechi care merge.
if ! "$PYTHON" -c "
import json
for f in ('config/sites_config.json','config/niche_policy.json','config/set_intelligence.json'):
    json.load(open(f, encoding='utf-8'))
" 2>/dev/null; then
  echo "configuratie invalida dupa pull — NU repornesc"
  exit 1
fi

if [ -n "$SERVICIU" ]; then
  echo "repornesc $SERVICIU"
  systemctl restart "$SERVICIU"
else
  echo "nu am gasit serviciul; repornesc manual"
  pkill -f "$PROIECT/main.py"
  sleep 3
  nohup "$PYTHON" "$PROIECT/main.py" >> "$PROIECT/bot.log" 2>&1 &
fi
echo "actualizat la $(git rev-parse --short HEAD)"
