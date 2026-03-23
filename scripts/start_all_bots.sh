#!/bin/bash
# Start all 167 bot configs in a SINGLE process via main_multi.py
# Uses shared ccxt exchange → significantly lower RAM vs individual processes
#
# Usage:
#   bash scripts/start_all_bots.sh        # start all
#   bash scripts/start_all_bots.sh stop   # stop all

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

echo "Starting 167 bots in single process (shared exchange)..."

# YOLO_MODE=1 required for BTC (5% risk) and WIF (3% risk)
# All 167 configs in ONE process — grouped by strategy type for readability
YOLO_MODE=1 nohup $PYTHON main_multi.py --configs \
    \
    config.json \
    config_wif.json \
    \
    config_arcusdt_ema.json \
    config_aliceusdt_ema1h.json \
    \
    config_avax_ichi.json \
    config_polusdt_ichi.json \
    config_gunusdt_ichi.json \
    config_berausdt_ichi.json \
    config_athusdt_ichi.json \
    config_injusdt_ichi.json \
    config_trumpusdt_ichi.json \
    config_animeusdt_ichi.json \
    config_ipusdt_ichi.json \
    config_near_ichi.json \
    config_zetausdt_ichi.json \
    \
    config_1000shibusdt_ichi4h.json \
    config_taousdt_ichi4h.json \
    config_renderusdt_ichi4h.json \
    config_arbusdt_ichi4h.json \
    config_signusdt_ichi4h.json \
    config_tiausdt_ichi4h.json \
    config_ondousdt_ichi4h.json \
    config_husdt_ichi4h.json \
    config_axsusdt_ichi4h.json \
    config_tonusdt_ichi4h.json \
    config_xmrusdt_ichi4h.json \
    config_hbarusdt_ichi4h.json \
    \
    config_algousdt_ichi4htrail.json \
    config_trxusdt_ichi4htrail.json \
    config_polyxusdt_ichi4htrail.json \
    config_fetusdt_ichi4htrail.json \
    config_xlmusdt_ichi4htrail.json \
    config_saharausdt_ichi4htrail.json \
    \
    config_mstrusdt_supertrend.json \
    config_xagusdt_supertrend.json \
    \
    config_1000pepeusdt_volexp.json \
    config_wldusdt_volexp.json \
    \
    config_aptusdt_emaichi4h.json \
    \
    config_ltcusdt_ichist4h.json \
    \
    config_lightusdt_alligator.json \
    \
    config_qntusdt_alligator4h.json \
    config_arcusdt_ichi.json \
    config_linkusdt_alligator4h.json \
    config_suiusdt_alligator4h.json \
    \
    config_lynusdt_dualst.json \
    config_humausdt_dualst.json \
    \
    config_enjusdt_dualst4h.json \
    config_xplusdt_dualst4h.json \
    config_xrpusdt_dualst4h.json \
    config_aktusdt_dualst4h.json \
    \
    config_aptusdt_ichiadx.json \
    config_enjusdt_ichiadx.json \
    config_ltcusdt_ichiadx.json \
    config_sandusdt_ichiadx.json \
    config_xmrusdt_ichiadx.json \
    \
    config_adausdt_stochmtf.json \
    config_arbusdt_stochmtf.json \
    config_atomusdt_stochmtf.json \
    config_axsusdt_stochmtf.json \
    config_cfxusdt_stochmtf.json \
    config_enausdt_stochmtf.json \
    config_injusdt_stochmtf.json \
    config_trumpusdt_stochmtf.json \
    \
    config_berausdt_emaribbon.json \
    config_cfxusdt_emaribbon.json \
    config_icpusdt_emaribbon.json \
    config_injusdt_emaribbon.json \
    config_1000shibusdt_emaribbon.json \
    config_axsusdt_emaribbon.json \
    config_kasusdt_emaribbon.json \
    config_opusdt_emaribbon.json \
    config_penguusdt_emaribbon.json \
    config_pippinusdt_emaribbon.json \
    config_trumpusdt_emaribbon.json \
    \
    config_dotusdt_ribbonao.json \
    config_pixelusdt_ribbonao.json \
    config_suiusdt_ribbonao.json \
    \
    config_aaveusdt_dualthrust.json \
    config_1000bonkusdt_dualthrust.json \
    config_aliceusdt_dualthrust.json \
    config_ankrusdt_dualthrust.json \
    config_aptusdt_dualthrust.json \
    config_arcusdt_dualthrust.json \
    config_atomusdt_dualthrust.json \
    config_avaxusdt_dualthrust.json \
    config_axsusdt_dualthrust.json \
    config_degousdt_dualthrust.json \
    config_dotusdt_dualthrust.json \
    config_enjusdt_dualthrust.json \
    config_fartcoinusdt_dualthrust.json \
    config_filusdt_dualthrust.json \
    config_galausdt_dualthrust.json \
    config_icpusdt_dualthrust.json \
    config_ipusdt_dualthrust.json \
    config_opusdt_dualthrust.json \
    config_penguusdt_dualthrust.json \
    config_phausdt_dualthrust.json \
    config_pippinusdt_dualthrust.json \
    config_pixelusdt_dualthrust.json \
    config_qntusdt_dualthrust.json \
    config_sandusdt_dualthrust.json \
    config_suiusdt_dualthrust.json \
    config_tiausdt_dualthrust.json \
    config_uniusdt_dualthrust.json \
    config_virtualusdt_dualthrust.json \
    config_vvvusdt_dualthrust.json \
    config_waxpusdt_dualthrust.json \
    config_wusdt_dualthrust.json \
    config_xrpusdt_dualthrust.json \
    config_zecusdt_dualthrust.json \
    config_zrousdt_dualthrust.json \
    \
    config_aaveusdt_rangebounce.json \
    config_aptusdt_rangebounce.json \
    config_axsusdt_rangebounce.json \
    config_cfxusdt_rangebounce.json \
    config_crvusdt_rangebounce.json \
    config_dotusdt_rangebounce.json \
    config_enjusdt_rangebounce.json \
    config_filusdt_rangebounce.json \
    config_icpusdt_rangebounce.json \
    config_kasusdt_rangebounce.json \
    config_ondousdt_rangebounce.json \
    config_penguusdt_rangebounce.json \
    config_sandusdt_rangebounce.json \
    config_suiusdt_rangebounce.json \
    config_tiausdt_rangebounce.json \
    config_tonusdt_rangebounce.json \
    config_vvvusdt_rangebounce.json \
    config_wusdt_rangebounce.json \
    config_xaiusdt_rangebounce.json \
    \
    config_adausdt_awesome.json \
    config_aliceusdt_awesome.json \
    config_axsusdt_awesome.json \
    config_bchusdt_awesome.json \
    config_cfxusdt_awesome.json \
    config_dashusdt_awesome.json \
    config_dotusdt_awesome.json \
    config_enjusdt_awesome.json \
    config_filusdt_awesome.json \
    config_galausdt_awesome.json \
    config_ondousdt_awesome.json \
    config_penguusdt_awesome.json \
    config_pixelusdt_awesome.json \
    config_sandusdt_awesome.json \
    config_tonusdt_awesome.json \
    config_virtualusdt_awesome.json \
    config_xaiusdt_awesome.json \
    config_zenusdt_awesome.json \
    \
    config_1000bonkusdt_zscore.json \
    config_1000shibusdt_zscore.json \
    config_aaveusdt_zscore.json \
    config_adausdt_zscore.json \
    config_aktusdt_zscore.json \
    config_animeusdt_zscore.json \
    config_banusdt_zscore.json \
    config_bnbusdt_zscore.json \
    config_dashusdt_zscore.json \
    config_ethfiusdt_zscore.json \
    config_injusdt_zscore.json \
    config_ondousdt_zscore.json \
    config_penguusdt_zscore.json \
    config_pixelusdt_zscore.json \
    config_polusdt_zscore.json \
    config_polyxusdt_zscore.json \
    config_taousdt_zscore.json \
    config_trumpusdt_zscore.json \
    config_xauusd_zscoresto.json \
    \
    > "$LOG_DIR/all_bots.log" 2>&1 &

sleep 10
PID=$(pgrep -f "main_multi.py" | head -1)
RAM=$(ps -o rss= -p $PID 2>/dev/null | awk '{printf "%.0f", $1/1024}')
echo ""
echo "========================================="
echo "  167 bots running in PID $PID"
echo "  RAM: ${RAM} MB (shared exchange)"
echo "  Log: $LOG_DIR/all_bots.log"
echo "  Dashboard: http://localhost:8501"
echo "========================================="
