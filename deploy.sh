#!/usr/bin/env bash
#
# Actualizeaza si reporneste botul pe Pi.
#
#   ./deploy.sh            actualizeaza si reporneste
#   ./deploy.sh --status   doar arata starea, nu schimba nimic
#   ./deploy.sh --stop     opreste botul
#   ./deploy.sh --logs     ultimele linii din log
#
# CE PROTEJEAZA
# Fisierele de stare (known_products, clasificari, verdicte Good/Bad, preturi)
# sunt salvate inainte de pull si puse la loc dupa. Fara asta, un pull care
# atinge config/ ti-ar sterge memoria botului si prima scanare ti-ar
# renotifica toate produsele deodata.
#
# config/sites_config.json e URMARIT de git, dar tools/descopera_categorii.py
# il modifica local. Scriptul pastreaza versiunea ta locala si o pune inapoi
# daca pull-ul o suprascrie — altfel ai pierde magazinele adaugate pe Pi.

set -uo pipefail

PROIECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROIECT" || exit 1

PYTHON="$PROIECT/venv/bin/python"
BACKUP="$PROIECT/.backup_stare"
LOG="$PROIECT/bot.log"
PIDFILE="$PROIECT/.bot.pid"
SERVICIU="pokemon-monitor"

# Daca serviciul systemd e instalat, el e seful: pornirea si oprirea trec
# prin systemctl. Altfel cadem pe nohup, ca inainte.
folosim_systemd() {
  systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICIU}.service"
}

# Fisiere de stare care NU au voie sa se piarda la actualizare.
STARE=(
  "config/known_products.json"
  "config/historical_products.json"
  "config/product_absence.json"
  "config/last_notified.json"
  "config/product_classifications.json"
  "config/rejected_products.json"
  "config/item_performance.json"
  "config/price_book.json"
  "config/alert_counts.json"
  "config/muted_sites.json"
  "config/beta.json"
  "config/sites_config.json"
)

verde()  { printf '\033[0;32m%s\033[0m\n' "$1"; }
galben() { printf '\033[0;33m%s\033[0m\n' "$1"; }
rosu()   { printf '\033[0;31m%s\033[0m\n' "$1"; }

pid_bot() {
  pgrep -f "$PROIECT/main.py" 2>/dev/null | head -1
}

opreste() {
  if folosim_systemd; then
    echo "Opresc serviciul $SERVICIU..."
    sudo systemctl stop "$SERVICIU"
    verde "Oprit."
    return 0
  fi
  local pid
  pid="$(pid_bot)"
  if [ -z "$pid" ]; then
    galben "Botul nu ruleaza."
    return 0
  fi
  echo "Opresc botul (pid $pid)..."
  kill "$pid" 2>/dev/null
  for _ in $(seq 1 15); do
    sleep 1
    [ -z "$(pid_bot)" ] && { verde "Oprit curat."; rm -f "$PIDFILE"; return 0; }
  done
  galben "Nu s-a oprit in 15s — il inchid fortat."
  kill -9 "$pid" 2>/dev/null
  rm -f "$PIDFILE"
}

porneste() {
  if folosim_systemd; then
    echo "Pornesc serviciul $SERVICIU..."
    sudo systemctl start "$SERVICIU"
    sleep 5
    if systemctl is-active --quiet "$SERVICIU"; then
      verde "Pornit prin systemd."
    else
      rosu "Nu a pornit. Vezi: sudo journalctl -u $SERVICIU -n 40"
      return 1
    fi
    return 0
  fi
  if [ -n "$(pid_bot)" ]; then
    galben "Botul deja ruleaza (pid $(pid_bot))."
    return 0
  fi
  [ -x "$PYTHON" ] || { rosu "Nu gasesc $PYTHON. Ai venv?"; return 1; }

  echo "Pornesc botul..."
  nohup "$PYTHON" "$PROIECT/main.py" >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 5

  local pid
  pid="$(pid_bot)"
  if [ -n "$pid" ]; then
    verde "Pornit (pid $pid)."
  else
    rosu "Nu a pornit. Ultimele linii din log:"
    tail -25 "$LOG"
    return 1
  fi
}

salveaza_stare() {
  rm -rf "$BACKUP"
  mkdir -p "$BACKUP/config"
  local n=0
  for f in "${STARE[@]}"; do
    [ -f "$f" ] && { cp -p "$f" "$BACKUP/$f"; n=$((n+1)); }
  done
  echo "Salvat $n fisiere de stare."
}

restaureaza_stare() {
  local n=0
  for f in "${STARE[@]}"; do
    if [ -f "$BACKUP/$f" ]; then
      # Punem inapoi doar daca pull-ul a sters sau schimbat fisierul.
      if [ ! -f "$f" ] || ! cmp -s "$BACKUP/$f" "$f"; then
        cp -p "$BACKUP/$f" "$f"
        n=$((n+1))
      fi
    fi
  done
  [ "$n" -gt 0 ] && galben "Am pus la loc $n fisiere de stare." || echo "Starea a ramas neatinsa."
}

stare() {
  local pid
  pid="$(pid_bot)"
  echo "Proiect : $PROIECT"
  if folosim_systemd; then
    echo "Serviciu: systemd ($(systemctl is-enabled $SERVICIU 2>/dev/null), $(systemctl is-active $SERVICIU 2>/dev/null))"
  else
    echo "Serviciu: neinstalat (pornire manuala)"
  fi
  echo "Commit  : $(git rev-parse --short HEAD 2>/dev/null) $(git log -1 --format=%s 2>/dev/null | cut -c1-50)"
  if [ -n "$pid" ]; then
    verde "Bot     : RULEAZA (pid $pid, de $(ps -o etime= -p "$pid" | tr -d ' '))"
  else
    rosu "Bot     : OPRIT"
  fi
  if [ -f config/known_products.json ]; then
    echo "Stare   : $("$PYTHON" -c "import json;d=json.load(open('config/known_products.json'));print(sum(len(v) for v in d.values()),'produse in',len(d),'magazine')" 2>/dev/null || echo '?')"
  fi
}

case "${1:-}" in
  --status) stare; exit 0 ;;
  --stop)   opreste; exit 0 ;;
  --logs)
    if folosim_systemd; then sudo journalctl -u "$SERVICIU" -f
    else tail -f "$LOG"; fi
    exit 0 ;;
esac

echo "════════════════════════════════════════════"
echo "  Actualizare bot"
echo "════════════════════════════════════════════"

salveaza_stare
opreste

echo
echo "Descarc din git..."
# Fisierele urmarite modificate local (sites_config) ar bloca pull-ul.
if ! git diff --quiet 2>/dev/null; then
  galben "Am modificari locale in fisiere urmarite — le pun deoparte."
  git stash push -m "deploy-$(date +%s)" >/dev/null 2>&1
fi

if ! git pull --ff-only; then
  rosu "Pull esuat. Rezolva manual, apoi ruleaza din nou."
  porneste
  exit 1
fi

restaureaza_stare

# Dependente noi?
if git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -q "requirements.txt"; then
  echo
  galben "requirements.txt s-a schimbat — instalez."
  "$PYTHON" -m pip install -q -r requirements.txt
fi

# Configuratia nu are voie sa fie stricata — botul ar porni si ar tacea.
echo
echo "Verific configuratia..."
if ! "$PYTHON" - <<'EOF'
import json, sys
for f in ("config/sites_config.json", "config/niche_policy.json",
          "config/set_intelligence.json"):
    try:
        json.load(open(f, encoding="utf-8"))
    except FileNotFoundError:
        print(f"  lipseste {f}")
    except Exception as e:
        print(f"  STRICAT {f}: {e}")
        sys.exit(1)
sites = json.load(open("config/sites_config.json", encoding="utf-8"))
print(f"  {len(sites)} magazine, {len({s.get('niche') for s in sites})} nise")
EOF
then
  rosu "Configuratie invalida. NU pornesc botul."
  exit 1
fi

echo
porneste

echo
stare
echo
echo "Log in direct:  ./deploy.sh --logs"
