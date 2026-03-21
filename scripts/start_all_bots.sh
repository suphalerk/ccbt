#!/bin/bash
# Start all 47 deployed bots locally
# Each bot runs as a background process with nohup
# Logs go to data/logs/bot_{symbol}.log

set -e
cd "$(dirname "$0")/.."

PYTHON=python3
LOG_DIR="data/logs"
mkdir -p "$LOG_DIR"

# Kill any existing bot processes first
echo "Stopping existing bots..."
pkill -f "python.*main.py.*--config" 2>/dev/null || true
sleep 2

echo "Starting 47 bots..."

# === EMA Crossover 15m (2 bots) ===
YOLO_MODE=1 nohup $PYTHON main.py --config config.json > "$LOG_DIR/bot_BTCUSDT.log" 2>&1 &
YOLO_MODE=1 nohup $PYTHON main.py --config config_wif.json > "$LOG_DIR/bot_WIFUSDT.log" 2>&1 &

# === EMA 15m mass expansion (1 bot) ===
YOLO_MODE=1 nohup $PYTHON main.py --config config_arcusdt_ema.json > "$LOG_DIR/bot_ARCUSDT_ema.log" 2>&1 &

# === Ichimoku Cloud 1H (8 bots) ===
nohup $PYTHON main.py --config config_avax_ichi.json > "$LOG_DIR/bot_AVAXUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_polusdt_ichi.json > "$LOG_DIR/bot_POLUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_gunusdt_ichi.json > "$LOG_DIR/bot_GUNUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_berausdt_ichi.json > "$LOG_DIR/bot_BERAUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_athusdt_ichi.json > "$LOG_DIR/bot_ATHUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_injusdt_ichi.json > "$LOG_DIR/bot_INJUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_trumpusdt_ichi.json > "$LOG_DIR/bot_TRUMPUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_animeusdt_ichi.json > "$LOG_DIR/bot_ANIMEUSDT.log" 2>&1 &

# === Ichimoku Cloud 1H — new coins (1 bot) ===
nohup $PYTHON main.py --config config_ipusdt_ichi.json > "$LOG_DIR/bot_IPUSDT.log" 2>&1 &

# === 4H Ichimoku Fixed TP (4 bots) ===
nohup $PYTHON main.py --config config_1000shibusdt_ichi4h.json > "$LOG_DIR/bot_1000SHIBUSDT_4h.log" 2>&1 &
nohup $PYTHON main.py --config config_taousdt_ichi4h.json > "$LOG_DIR/bot_TAOUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_renderusdt_ichi4h.json > "$LOG_DIR/bot_RENDERUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_arbusdt_ichi4h.json > "$LOG_DIR/bot_ARBUSDT_4h.log" 2>&1 &

# === Ichimoku Cloud 4H — new coins (5 bots) ===
nohup $PYTHON main.py --config config_signusdt_ichi4h.json > "$LOG_DIR/bot_SIGNUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_tiausdt_ichi4h.json > "$LOG_DIR/bot_TIAUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_ondousdt_ichi4h.json > "$LOG_DIR/bot_ONDOUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_husdt_ichi4h.json > "$LOG_DIR/bot_HUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_axsusdt_ichi4h.json > "$LOG_DIR/bot_AXSUSDT.log" 2>&1 &

# === 4H Ichimoku Trailing (6 bots) ===
nohup $PYTHON main.py --config config_algousdt_ichi4htrail.json > "$LOG_DIR/bot_ALGOUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_trxusdt_ichi4htrail.json > "$LOG_DIR/bot_TRXUSDT_4ht.log" 2>&1 &
nohup $PYTHON main.py --config config_polyxusdt_ichi4htrail.json > "$LOG_DIR/bot_POLYXUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_fetusdt_ichi4htrail.json > "$LOG_DIR/bot_FETUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_xlmusdt_ichi4htrail.json > "$LOG_DIR/bot_XLMUSDT_4ht.log" 2>&1 &
nohup $PYTHON main.py --config config_saharausdt_ichi4htrail.json > "$LOG_DIR/bot_SAHARAUSDT_4ht.log" 2>&1 &

# === Supertrend 1H (2 bots) ===
nohup $PYTHON main.py --config config_mstrusdt_supertrend.json > "$LOG_DIR/bot_MSTRUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_xagusdt_supertrend.json > "$LOG_DIR/bot_XAGUSDT.log" 2>&1 &

# === Vol Expansion Breakout 1H (2 bots) ===
nohup $PYTHON main.py --config config_1000pepeusdt_volexp.json > "$LOG_DIR/bot_1000PEPEUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_wldusdt_volexp.json > "$LOG_DIR/bot_WLDUSDT.log" 2>&1 &

# === EMA+Ichimoku Hybrid 4H (3 bots, upgraded) ===
nohup $PYTHON main.py --config config_aptusdt_emaichi4h.json > "$LOG_DIR/bot_APTUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_near_ichi.json > "$LOG_DIR/bot_NEARUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_zetausdt_ichi.json > "$LOG_DIR/bot_ZETAUSDT.log" 2>&1 &

# === Ichi+Supertrend Hybrid 4H (1 bot) ===
nohup $PYTHON main.py --config config_ltcusdt_ichist4h.json > "$LOG_DIR/bot_LTCUSDT.log" 2>&1 &

# === Alligator 1H (1 bot) ===
nohup $PYTHON main.py --config config_lightusdt_alligator.json > "$LOG_DIR/bot_LIGHTUSDT.log" 2>&1 &

# === Alligator 4H (5 bots, 2 upgraded) ===
nohup $PYTHON main.py --config config_hbarusdt_ichi4h.json > "$LOG_DIR/bot_HBARUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_qntusdt_alligator4h.json > "$LOG_DIR/bot_QNTUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_arcusdt_ichi.json > "$LOG_DIR/bot_ARCUSDT_alli.log" 2>&1 &
nohup $PYTHON main.py --config config_linkusdt_alligator4h.json > "$LOG_DIR/bot_LINKUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_suiusdt_alligator4h.json > "$LOG_DIR/bot_SUIUSDT.log" 2>&1 &

# === Dual Supertrend 1H (2 bots) ===
nohup $PYTHON main.py --config config_lynusdt_dualst.json > "$LOG_DIR/bot_LYNUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_humausdt_dualst.json > "$LOG_DIR/bot_HUMAUSDT.log" 2>&1 &

# === Dual Supertrend 4H (4 bots) ===
nohup $PYTHON main.py --config config_enjusdt_dualst4h.json > "$LOG_DIR/bot_ENJUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_xplusdt_dualst4h.json > "$LOG_DIR/bot_XPLUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_xrpusdt_dualst4h.json > "$LOG_DIR/bot_XRPUSDT.log" 2>&1 &
nohup $PYTHON main.py --config config_aktusdt_dualst4h.json > "$LOG_DIR/bot_AKTUSDT.log" 2>&1 &

# Wait a moment then count
sleep 5
RUNNING=$(pgrep -f "python.*main.py.*--config" | wc -l)
echo ""
echo "========================================="
echo "  $RUNNING / 47 bots started"
echo "  Logs: $LOG_DIR/bot_*.log"
echo "  Dashboard: http://localhost:8501"
echo "========================================="
