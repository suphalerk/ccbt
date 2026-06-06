#!/usr/bin/env bash
# start.sh — Wrapper to launch the CCBT trading bot
# Loads .env, activates venv if present, then runs main.py

set -euo pipefail

PROJECT_DIR="/Users/iceai/Work/ccbt"
cd "$PROJECT_DIR"

# Load environment variables from .env if it exists
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.env"
    set +a
else
    echo "WARNING: .env file not found at $PROJECT_DIR/.env" >&2
    echo "Bot will start but API keys will be missing." >&2
fi

# Activate virtual environment if one exists
if [[ -f "$PROJECT_DIR/.venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.venv/bin/activate"
elif [[ -f "$PROJECT_DIR/venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/venv/bin/activate"
fi

# Resolve python — prefer venv python, fall back to homebrew, then system
PYTHON="python3"
if command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Starting CCBT trading bot"
echo "  Python:  $PYTHON"
echo "  Working: $PROJECT_DIR"

# Route exchange traffic through the DigitalOcean SOCKS proxy (fixed egress IP
# for the Binance whitelist). Defaults to the local autossh tunnel; .env may
# override or unset it. bot/exchange.py + bot/shared_exchange_pool.py read this.
export CCBT_SOCKS_PROXY="${CCBT_SOCKS_PROXY:-socks5h://127.0.0.1:1080}"

# Pre-flight: refuse to start unless the proxy egresses the whitelisted IP.
# launchd (SuccessfulExit=false) will retry every ThrottleInterval until the
# tunnel is healthy — never trade with the wrong outbound IP.
if [[ -n "$CCBT_SOCKS_PROXY" ]]; then
    echo "  Proxy:   $CCBT_SOCKS_PROXY"
    if ! bash "$PROJECT_DIR/deploy/do-proxy/check-proxy.sh"; then
        echo "ERROR: SOCKS proxy check failed — refusing to start bots" >&2
        exit 1
    fi
fi

# 96 walk-forward audited bots (updated 2026-03-24)
AUDITED_CONFIGS="config.json config_1000bonkusdt_dualthrust.json config_1000pepeusdt_volexp.json config_1000shibusdt_emaribbon.json config_1000shibusdt_zscore.json config_aaveusdt_dualthrust.json config_aaveusdt_rangebounce.json config_aaveusdt_zscore.json config_adausdt_stochmtf.json config_aktusdt_zscore.json config_aliceusdt_awesome.json config_ankrusdt_dualthrust.json config_aptusdt_rangebounce.json config_arbusdt_stochmtf.json config_arcusdt_ema.json config_atomusdt_dualthrust.json config_atomusdt_stochmtf.json config_axsusdt_awesome.json config_axsusdt_dualthrust.json config_axsusdt_emaribbon.json config_axsusdt_rangebounce.json config_axsusdt_stochmtf.json config_banusdt_zscore.json config_bchusdt_awesome.json config_berausdt_emaribbon.json config_berausdt_ichi.json config_bnbusdt_zscore.json config_dotusdt_awesome.json config_dotusdt_dualthrust.json config_dotusdt_rangebounce.json config_enjusdt_awesome.json config_enjusdt_dualthrust.json config_enjusdt_rangebounce.json config_fartcoinusdt_dualthrust.json config_filusdt_awesome.json config_filusdt_dualthrust.json config_filusdt_rangebounce.json config_icpusdt_dualthrust.json config_icpusdt_emaribbon.json config_icpusdt_rangebounce.json config_injusdt_emaribbon.json config_injusdt_zscore.json config_ipusdt_dualthrust.json config_ipusdt_ichi.json config_kasusdt_emaribbon.json config_ondousdt_ichi4h.json config_ondousdt_rangebounce.json config_phausdt_dualthrust.json config_pippinusdt_dualthrust.json config_pippinusdt_emaribbon.json config_pixelusdt_awesome.json config_pixelusdt_dualthrust.json config_pixelusdt_zscore.json config_polusdt_ichi4htrail.json config_polyxusdt_zscore.json config_sandusdt_awesome.json config_sandusdt_rangebounce.json config_suiusdt_dualthrust.json config_suiusdt_rangebounce.json config_taousdt_zscore.json config_tonusdt_awesome.json config_tonusdt_rangebounce.json config_trumpusdt_emaribbon.json config_trumpusdt_ichi.json config_trumpusdt_stochmtf.json config_trumpusdt_zscore.json config_uniusdt_dualthrust.json config_wif.json config_xaiusdt_awesome.json config_xaiusdt_rangebounce.json config_xrpusdt_dualthrust.json config_zenusdt_awesome.json"

exec "$PYTHON" main_multi.py --configs $AUDITED_CONFIGS --max-positions 10 --risk 0.01 --leverage 25
