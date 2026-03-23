#!/bin/bash
# Start all 47 deployed bots in a SINGLE process via main_multi.py
# Uses shared ccxt exchange → 374 MB RAM total instead of 23 GB (Docker)
#
# Usage:
#   bash scripts/start_all_bots.sh        # start all
#   bash scripts/start_all_bots.sh stop    # stop all

set -e
cd "$(dirname "$0")/.."

PYTHON=python3
LOG_DIR="data/logs"
mkdir -p "$LOG_DIR"

if [ "$1" = "stop" ]; then
    echo "Stopping all bots..."
    pkill -9 -f "Python.*main_multi.py" 2>/dev/null || true
    pkill -9 -f "Python.*main.py.*--config" 2>/dev/null || true
    sleep 2
    echo "All bots stopped."
    exit 0
fi

# Kill any existing bot processes
echo "Stopping existing bots..."
pkill -9 -f "Python.*main_multi.py" 2>/dev/null || true
pkill -9 -f "Python.*main.py.*--config" 2>/dev/null || true
sleep 2

echo "Starting 47 bots in single process (shared exchange)..."

# All 47 deployed bots in ONE process = ~374 MB RAM
YOLO_MODE=1 nohup $PYTHON main_multi.py --configs \
    config.json config_wif.json config_arcusdt_ema.json \
    config_avax_ichi.json config_polusdt_ichi.json config_gunusdt_ichi.json \
    config_berausdt_ichi.json config_athusdt_ichi.json config_injusdt_ichi.json \
    config_trumpusdt_ichi.json config_animeusdt_ichi.json config_ipusdt_ichi.json \
    config_1000shibusdt_ichi4h.json config_taousdt_ichi4h.json config_renderusdt_ichi4h.json \
    config_arbusdt_ichi4h.json config_signusdt_ichi4h.json config_tiausdt_ichi4h.json \
    config_ondousdt_ichi4h.json config_husdt_ichi4h.json config_axsusdt_ichi4h.json \
    config_algousdt_ichi4htrail.json config_trxusdt_ichi4htrail.json \
    config_polyxusdt_ichi4htrail.json config_fetusdt_ichi4htrail.json \
    config_xlmusdt_ichi4htrail.json config_saharausdt_ichi4htrail.json \
    config_mstrusdt_supertrend.json config_xagusdt_supertrend.json \
    config_1000pepeusdt_volexp.json config_wldusdt_volexp.json \
    config_aptusdt_emaichi4h.json config_near_ichi.json config_zetausdt_ichi.json \
    config_ltcusdt_ichist4h.json config_lightusdt_alligator.json \
    config_hbarusdt_ichi4h.json config_qntusdt_alligator4h.json \
    config_arcusdt_ichi.json config_linkusdt_alligator4h.json \
    config_suiusdt_alligator4h.json config_lynusdt_dualst.json \
    config_humausdt_dualst.json config_enjusdt_dualst4h.json \
    config_xplusdt_dualst4h.json config_xrpusdt_dualst4h.json \
    config_aktusdt_dualst4h.json \
    > "$LOG_DIR/all_bots.log" 2>&1 &

sleep 10
PID=$(pgrep -f "main_multi.py" | head -1)
RAM=$(ps -o rss= -p $PID 2>/dev/null | awk '{printf "%.0f", $1/1024}')
echo ""
echo "========================================="
echo "  47 bots running in PID $PID"
echo "  RAM: ${RAM} MB (shared exchange)"
echo "  Log: $LOG_DIR/all_bots.log"
echo "  Dashboard: http://localhost:8501"
echo "========================================="
