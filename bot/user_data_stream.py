"""User-data WebSocket stream task for realtime SL/TP close detection.

PR2 — WS client (REST + parse, proxied), behind ``CCBT_USERDATA_WS=1``.

This module provides ONE process-level WS task that listens to the Binance
USDⓈ-M Futures user-data stream.  When a terminal position event fires (SL/TP
FILLED, or position amount → 0), it ``asyncio.Event.set()``s the relevant
per-engine wake events registered in *wake_events*.  It is a **TRIGGER ONLY**:

- Never sends Telegram alerts.
- Never writes to the DB.
- Never computes PnL.

The owning engine wakes early, re-fetches real position state, and drives the
existing authoritative ``check_closed_positions`` path as usual.

Usage (created by main_multi.py when CCBT_USERDATA_WS==1)::

    task = asyncio.create_task(
        run_user_data_stream(shutdown_event, shared_exchange, wake_events),
        name="user-data-ws",
    )

Design notes
------------
- Dedicated ccxt instance is created here for all listenKey REST calls
  (fapiPrivatePostListenKey/PutListenKey/DeleteListenKey).  The shared trading
  ccxt is NEVER touched from this task.
- Testnet vs mainnet is derived from the shared_exchange object's URLs — NOT
  from markprice._is_testnet().
- SOCKS pre-flight: if CCBT_SOCKS_PROXY is set and the python-socks connector
  cannot be imported, the task returns immediately (fail-closed, IP-leak
  hazard).  If CCBT_SOCKS_PROXY is unset, connect directly (dev/testnet).
- run_forever + exponential backoff mirrors api/markprice.py:366-388.
- Two independent timers: (1) listenKey keepalive PUT every ~1800s;
  (2) 24h WS cap handled naturally by run_forever reconnect.
- STALE watchdog: if no frame arrives for STALE_AFTER_S seconds, reconnect.
- Reconnect reconcile sweep: on (re)connect, debounced sweep sets wake events
  for all coins with open DB trades (keyed on journal, not _tracked_trades).
- Shutdown: attempt listenKey DELETE within 2s, swallow all failures; prefer
  letting the key expire to avoid invalidating a sibling process's stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Binance testnet user-data WS base (no trailing slash; append /<listenKey>)
_TESTNET_WS_BASE = "wss://stream.binancefuture.com/ws"

#: Binance mainnet user-data WS base
_MAINNET_WS_BASE = "wss://fstream.binance.com/ws"

#: Binance testnet fapiPrivate REST base domain (used for startup assertion)
_TESTNET_REST_DOMAIN = "testnet.binancefuture.com"

#: Binance mainnet fapiPrivate REST base domain
_MAINNET_REST_DOMAIN = "fstream.binance.com"

#: Keepalive PUT interval (Binance listenKey TTL is 60 min; PUT every ~30 min)
LISTEN_KEY_KEEPALIVE_S: int = 1800

#: Seconds without a frame before we close + reconnect (user-data is sparse)
STALE_AFTER_S: int = 60

#: Exponential backoff base/max in seconds
BACKOFF_BASE_S: float = 1.0
MAX_BACKOFF_S: float = 60.0

#: Minimum seconds between reconnect reconcile sweeps (debounce)
RECONCILE_DEBOUNCE_S: float = 30.0

#: Terminal order types that trigger a wake
_TERMINAL_ORDER_TYPES: frozenset[str] = frozenset(
    {"STOP_MARKET", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET", "LIQUIDATION"}
)


# ---------------------------------------------------------------------------
# Helper: derive testnet flag from shared exchange object
# ---------------------------------------------------------------------------

def _is_testnet_from_exchange(shared_exchange) -> bool:
    """Derive testnet flag from the shared ccxt exchange's fapiPrivate URL.

    We inspect the URL rather than trusting a stored boolean, so the result
    is always consistent with the actual endpoint being used.
    """
    try:
        fapi_url: str = shared_exchange.urls["api"].get("fapiPrivate", "")
        return _TESTNET_REST_DOMAIN in fapi_url
    except Exception:
        # Fallback: inspect apiKey to infer (crude) — if neither works,
        # default to testnet (safe).
        return True


def _get_exchange_credentials(shared_exchange) -> tuple[str, str, Optional[str]]:
    """Extract apiKey, secret, and optional SOCKS proxy from shared exchange."""
    api_key: str = getattr(shared_exchange, "apiKey", "") or ""
    secret: str = getattr(shared_exchange, "secret", "") or ""
    proxy: Optional[str] = getattr(shared_exchange, "socksProxy", None) or None
    return api_key, secret, proxy


# ---------------------------------------------------------------------------
# Helper: build dedicated ccxt instance
# ---------------------------------------------------------------------------

def _build_dedicated_exchange(shared_exchange):
    """Build a second ccxt.binance instance for listenKey REST only.

    Uses the same apiKey/secret/socksProxy and testnet flag as the shared
    exchange, but a completely separate ccxt object with its own
    requests.Session.  This instance is used ONLY for fapiPrivate*ListenKey
    calls via asyncio.to_thread.  The shared trading ccxt is never touched.
    """
    try:
        import ccxt  # noqa: F401 (checked at module level in caller)
    except ImportError:
        return None

    api_key, secret, proxy = _get_exchange_credentials(shared_exchange)
    use_testnet = _is_testnet_from_exchange(shared_exchange)

    params: dict = {
        "apiKey": api_key,
        "secret": secret,
        "enableRateLimit": False,  # listenKey calls are infrequent; no need
        "options": {"defaultType": "future"},
    }

    exchange = ccxt.binance(params)

    if use_testnet:
        base = "https://testnet.binancefuture.com"
        exchange.urls["api"]["fapiPublic"] = f"{base}/fapi/v1"
        exchange.urls["api"]["fapiPrivate"] = f"{base}/fapi/v1"
        exchange.urls["api"]["fapiPrivateV2"] = f"{base}/fapi/v2"
        exchange.has["fetchCurrencies"] = False
        exchange.has["fetchMarginMarkets"] = False

    if proxy:
        exchange.socksProxy = proxy

    return exchange


# ---------------------------------------------------------------------------
# Symbol normalisation
# ---------------------------------------------------------------------------

def _normalize_coin(raw_symbol: str) -> str:
    """Normalise a raw Binance symbol to the wake_events key format.

    Binance sends 'BTCUSDT'; wake_events is keyed the same way.
    """
    return raw_symbol.upper()


# ---------------------------------------------------------------------------
# Frame handler (pure, sync, set-only)
# ---------------------------------------------------------------------------

def _handle_frame(raw: str, wake_events: dict) -> None:
    """Parse one WS frame and set relevant wake events.

    This function is the ONLY place that fires wake events.  It is:
    - Pure (no IO, no DB, no Telegram)
    - Idempotent (Event.set() is safe to call repeatedly)
    - Set-only (never reads recently_closed, never alerts)

    Args:
        raw: Raw JSON string from the WS.
        wake_events: Shared dict mapping normalised coin symbols to lists of
            per-engine asyncio.Event objects.
    """
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return

    event_type = msg.get("e", "")
    coins_to_wake: set[str] = set()

    if event_type == "ORDER_TRADE_UPDATE":
        order = msg.get("o", {})
        status = order.get("X", "")         # e.g. "FILLED"
        is_reduce = order.get("R", False)   # reduceOnly
        order_type = order.get("o", "")     # e.g. "STOP_MARKET"
        symbol = order.get("s", "")         # e.g. "BTCUSDT"

        if (
            status == "FILLED"
            and is_reduce is True
            and order_type in _TERMINAL_ORDER_TYPES
        ):
            coin = _normalize_coin(symbol)
            if coin:
                coins_to_wake.add(coin)

    elif event_type == "ACCOUNT_UPDATE":
        account = msg.get("a", {})
        positions = account.get("P", [])
        for pos in positions:
            pa = pos.get("pa", None)  # position amount
            symbol = pos.get("s", "") or pos.get("S", "")
            # pa=="0" means truly flat (not partial TP)
            if str(pa) == "0" and symbol:
                coin = _normalize_coin(symbol)
                if coin:
                    coins_to_wake.add(coin)

    for coin in coins_to_wake:
        events = wake_events.get(coin, [])
        for evt in events:
            try:
                evt.set()
            except Exception:
                pass  # never crash the WS loop on a bad Event reference


# ---------------------------------------------------------------------------
# Reconcile sweep (fires on (re)connect, keyed on open DB trades)
# ---------------------------------------------------------------------------

async def _reconcile_sweep(
    wake_events: dict,
    journal,
    last_sweep_time: list,  # mutable single-element list used as ref
) -> None:
    """Set wake events for all coins with open DB trades.

    Called on every _run_once startup to catch closes that happened while
    the stream was down.  Debounced by RECONCILE_DEBOUNCE_S.

    Args:
        wake_events: Shared wake events dict.
        journal: A TradeLogger instance with .get_open_trades().
        last_sweep_time: Single-element list holding the last sweep timestamp.
    """
    now = time.monotonic()
    if now - last_sweep_time[0] < RECONCILE_DEBOUNCE_S:
        logger.debug("user_data_ws: reconcile sweep debounced")
        return

    last_sweep_time[0] = now

    try:
        open_trades = journal.get_open_trades()
    except Exception as exc:
        logger.warning("user_data_ws: reconcile sweep get_open_trades failed: %s", exc)
        return

    fired_coins: set[str] = set()
    for trade in open_trades:
        raw_symbol = trade.get("symbol", "")
        if not raw_symbol:
            continue
        # Normalise from DB format (e.g. 'BTC/USDT:USDT') to wake_events key
        coin = (
            raw_symbol
            .replace("/", "")
            .replace(":USDT", "")
            .replace("-", "")
            .upper()
        )
        # Only fire for coins we actually track
        events = wake_events.get(coin, [])
        for evt in events:
            try:
                evt.set()
                fired_coins.add(coin)
            except Exception:
                pass

    if fired_coins:
        logger.info(
            "user_data_ws: reconcile sweep fired wakes for %d coin(s): %s",
            len(fired_coins),
            sorted(fired_coins),
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_user_data_stream(
    shutdown_event: asyncio.Event,
    shared_exchange,
    wake_events: dict,
) -> None:
    """Account-wide user-data WS trigger task.

    Creates ONE listenKey / ONE WS for the whole process.  Wakes per-engine
    asyncio.Events on SL/TP fills or position-amount-zero events.

    Args:
        shutdown_event: Global shutdown signal; when set, task tears down.
        shared_exchange: The shared trading ccxt instance (used READ-ONLY to
            derive credentials and testnet flag; never called for listenKey).
        wake_events: Shared dict mapping normalised coin symbols to lists of
            per-engine asyncio.Event objects.
    """
    # ------------------------------------------------------------------
    # 0. Guard: optional deps
    # ------------------------------------------------------------------
    try:
        import websockets  # noqa: F401
    except ImportError:
        logger.warning(
            "user_data_ws: websockets not installed — WS wake feature disabled"
        )
        return

    try:
        import ccxt as _ccxt  # noqa: F401
    except ImportError:
        logger.warning(
            "user_data_ws: ccxt not installed — WS wake feature disabled"
        )
        return

    # ------------------------------------------------------------------
    # 1. Guard: credentials
    # ------------------------------------------------------------------
    api_key, secret, socks_proxy = _get_exchange_credentials(shared_exchange)
    if not api_key or not secret:
        logger.warning(
            "user_data_ws: API credentials missing — WS wake feature disabled"
        )
        return

    # ------------------------------------------------------------------
    # 2. Derive testnet flag + WS base URL
    # ------------------------------------------------------------------
    use_testnet = _is_testnet_from_exchange(shared_exchange)
    ws_base = _TESTNET_WS_BASE if use_testnet else _MAINNET_WS_BASE
    rest_domain = _TESTNET_REST_DOMAIN if use_testnet else _MAINNET_REST_DOMAIN

    logger.info(
        "user_data_ws: starting (testnet=%s, ws_base=%s)",
        use_testnet,
        ws_base,
    )

    # ------------------------------------------------------------------
    # 3. SOCKS pre-flight (fail-closed)
    # ------------------------------------------------------------------
    socks_proxy_str = socks_proxy or os.getenv("CCBT_SOCKS_PROXY", "").strip()
    ws_connect_kwargs: dict = {}

    if socks_proxy_str:
        # Lazy import python-socks connector for websockets
        try:
            from python_socks.async_.asyncio import Proxy as _SocksProxy  # noqa: F401

            # Build a proxy connector factory for websockets
            from python_socks.async_.asyncio import Proxy

            def _make_socks_connector(proxy_url: str):
                """Return a websockets sock= factory using python-socks."""
                import socket

                async def _connect(uri, **kw):
                    import websockets as _ws
                    proxy = Proxy.from_url(proxy_url, rdns=True)
                    # python_socks provides connect() which returns a socket
                    host = uri.host
                    port = uri.port or (443 if uri.secure else 80)
                    sock = await proxy.connect(dest_host=host, dest_port=port)
                    return await _ws.connect(
                        str(uri),
                        sock=sock,
                        **kw,
                    )

                return _connect

            ws_connect_kwargs["_socks_proxy_url"] = socks_proxy_str
            logger.info(
                "user_data_ws: SOCKS proxy configured: %s", socks_proxy_str
            )
        except ImportError as exc:
            logger.warning(
                "user_data_ws: CCBT_SOCKS_PROXY is set but python-socks import "
                "failed (%s) — refusing to open WS without proxy (IP-leak hazard). "
                "WS wake feature disabled.",
                exc,
            )
            return
        except Exception as exc:
            logger.warning(
                "user_data_ws: SOCKS pre-flight failed (%s) — WS wake feature disabled.",
                exc,
            )
            return

    # ------------------------------------------------------------------
    # 4. Build dedicated ccxt instance for listenKey REST
    # ------------------------------------------------------------------
    dedicated_ex = _build_dedicated_exchange(shared_exchange)
    if dedicated_ex is None:
        logger.warning(
            "user_data_ws: could not build dedicated exchange — WS wake feature disabled"
        )
        return

    # Startup assertion: dedicated exchange REST domain matches WS domain
    try:
        ded_fapi_url: str = dedicated_ex.urls["api"].get("fapiPrivate", "")
        assert rest_domain in ded_fapi_url, (
            f"user_data_ws: WS host domain '{rest_domain}' does not match "
            f"dedicated ccxt fapiPrivate URL '{ded_fapi_url}' — "
            "testnet/mainnet mismatch detected"
        )
    except AssertionError as exc:
        logger.error("user_data_ws: %s", exc)
        return

    # ------------------------------------------------------------------
    # 5. Build journal for reconnect reconcile sweep
    # ------------------------------------------------------------------
    journal = None
    try:
        from bot.logger import TradeLogger

        db_path = os.getenv("BOT_DATA_DIR", ".")
        journal = TradeLogger(db_path=db_path)
    except Exception as exc:
        logger.warning(
            "user_data_ws: could not open journal for reconcile sweep (%s) — "
            "sweep will be disabled",
            exc,
        )

    # ------------------------------------------------------------------
    # 6. Mutable state shared across run_forever iterations
    # ------------------------------------------------------------------
    listen_key: list[Optional[str]] = [None]   # mutable ref
    retry_count: list[int] = [0]
    last_sweep_time: list[float] = [-RECONCILE_DEBOUNCE_S - 1.0]  # force first sweep
    force_reconnect: list[bool] = [False]

    # ------------------------------------------------------------------
    # 7. run_forever with exponential backoff
    # ------------------------------------------------------------------
    while True:
        if shutdown_event.is_set():
            break

        try:
            await _run_once(
                shutdown_event=shutdown_event,
                dedicated_ex=dedicated_ex,
                ws_base=ws_base,
                socks_proxy_str=socks_proxy_str,
                wake_events=wake_events,
                listen_key=listen_key,
                force_reconnect=force_reconnect,
                journal=journal,
                last_sweep_time=last_sweep_time,
            )
        except asyncio.CancelledError:
            logger.info("user_data_ws: cancelled — stopping")
            # Attempt listenKey DELETE with a 2-second bound; prefer letting
            # the key expire to avoid invalidating a sibling process's stream.
            if listen_key[0]:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            dedicated_ex.fapiPrivateDeleteListenKey,
                            {"listenKey": listen_key[0]},
                        ),
                        timeout=2.0,
                    )
                except Exception:
                    pass  # swallow — prefer expiry over blocking shutdown
            raise
        except Exception as exc:
            logger.warning("user_data_ws: connection error: %s", exc)

        if shutdown_event.is_set():
            break

        # Exponential backoff
        delay = min(BACKOFF_BASE_S * (2 ** retry_count[0]), MAX_BACKOFF_S)
        retry_count[0] += 1
        logger.info(
            "user_data_ws: reconnecting in %.1fs (attempt #%d)",
            delay,
            retry_count[0],
        )
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    # Final shutdown: best-effort DELETE
    if listen_key[0]:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    dedicated_ex.fapiPrivateDeleteListenKey,
                    {"listenKey": listen_key[0]},
                ),
                timeout=2.0,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Single WS session
# ---------------------------------------------------------------------------

async def _run_once(
    shutdown_event: asyncio.Event,
    dedicated_ex,
    ws_base: str,
    socks_proxy_str: Optional[str],
    wake_events: dict,
    listen_key: list,
    force_reconnect: list,
    journal,
    last_sweep_time: list,
) -> None:
    """Single WS session.  Exits (raises or returns) when connection drops."""
    import websockets  # defer import so module is testable without websockets

    force_reconnect[0] = False

    # ------------------------------------------------------------------
    # 1. Obtain / refresh listenKey
    # ------------------------------------------------------------------
    if listen_key[0] is None:
        try:
            result = await asyncio.to_thread(
                dedicated_ex.fapiPrivatePostListenKey, {}
            )
            listen_key[0] = result.get("listenKey", "")
            logger.info("user_data_ws: listenKey obtained")
        except Exception as exc:
            err_str = str(exc)
            if "-1125" in err_str or "listenKey does not exist" in err_str.lower():
                # Another process may own this key; try POST fresh
                logger.warning(
                    "user_data_ws: -1125 on initial listenKey GET — treating as "
                    "'another process owns this key'; POSTing fresh key"
                )
                try:
                    result = await asyncio.to_thread(
                        dedicated_ex.fapiPrivatePostListenKey, {}
                    )
                    listen_key[0] = result.get("listenKey", "")
                except Exception as exc2:
                    logger.error("user_data_ws: failed to obtain listenKey: %s", exc2)
                    return
            else:
                logger.error("user_data_ws: failed to obtain listenKey: %s", exc)
                return

    if not listen_key[0]:
        logger.error("user_data_ws: listenKey is empty — cannot connect")
        return

    # ------------------------------------------------------------------
    # 2. Connect to WS
    # ------------------------------------------------------------------
    ws_url = f"{ws_base}/{listen_key[0]}"
    logger.info("user_data_ws: connecting to %s", ws_url)

    # Build connect kwargs; SOCKS path uses a raw socket
    if socks_proxy_str:
        try:
            from python_socks.async_.asyncio import Proxy as _Proxy
            proxy = _Proxy.from_url(socks_proxy_str, rdns=True)
            uri = websockets.uri.parse_uri(ws_url)
            host = uri.host
            port = uri.port or (443 if uri.secure else 80)
            sock = await proxy.connect(dest_host=host, dest_port=port)
            ws_cm = websockets.connect(ws_url, sock=sock, ping_interval=20, ping_timeout=10)
        except Exception as exc:
            logger.warning(
                "user_data_ws: SOCKS connect failed (%s) — refusing to open "
                "raw WS (IP-leak hazard); returning",
                exc,
            )
            return
    else:
        ws_cm = websockets.connect(ws_url, ping_interval=20, ping_timeout=10)

    # ------------------------------------------------------------------
    # 3. Keepalive task
    # ------------------------------------------------------------------
    async def _keepalive_loop() -> None:
        """PUT listenKey every LISTEN_KEY_KEEPALIVE_S seconds."""
        while True:
            await asyncio.sleep(LISTEN_KEY_KEEPALIVE_S)
            try:
                await asyncio.to_thread(
                    dedicated_ex.fapiPrivatePutListenKey,
                    {"listenKey": listen_key[0]},
                )
                logger.debug("user_data_ws: listenKey keepalive PUT OK")
            except Exception as exc:
                err_str = str(exc)
                if "-1125" in err_str or "listenKey does not exist" in err_str.lower():
                    logger.warning(
                        "user_data_ws: listenKey expired (-1125) — POSTing fresh key"
                    )
                    try:
                        result = await asyncio.to_thread(
                            dedicated_ex.fapiPrivatePostListenKey, {}
                        )
                        listen_key[0] = result.get("listenKey", "")
                        force_reconnect[0] = True
                        logger.info(
                            "user_data_ws: fresh listenKey obtained, will reconnect"
                        )
                    except Exception as exc2:
                        logger.error(
                            "user_data_ws: failed to recreate listenKey: %s", exc2
                        )
                else:
                    logger.warning(
                        "user_data_ws: listenKey keepalive PUT failed: %s", exc
                    )

    keepalive_task = asyncio.create_task(_keepalive_loop(), name="user-data-ws-keepalive")

    # ------------------------------------------------------------------
    # 4. Reconcile sweep on (re)connect
    # ------------------------------------------------------------------
    if journal is not None:
        asyncio.create_task(
            _reconcile_sweep(wake_events, journal, last_sweep_time),
            name="user-data-ws-reconcile",
        )

    # ------------------------------------------------------------------
    # 5. Read frames
    # ------------------------------------------------------------------
    try:
        async with ws_cm as ws:
            logger.info("user_data_ws: connected — listening for user-data events")
            last_frame_time = time.monotonic()

            while True:
                if shutdown_event.is_set():
                    return
                if force_reconnect[0]:
                    logger.info("user_data_ws: force reconnect flagged — closing session")
                    return

                # Stale watchdog + frame receive with timeout
                time_since_frame = time.monotonic() - last_frame_time
                remaining = max(0.0, STALE_AFTER_S - time_since_frame)

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining + 1.0)
                    last_frame_time = time.monotonic()
                    try:
                        _handle_frame(raw, wake_events)
                    except Exception as exc:
                        logger.debug("user_data_ws: frame handler error: %s", exc)
                except asyncio.TimeoutError:
                    pass  # check stale watchdog next iteration

                # Stale watchdog check
                if time.monotonic() - last_frame_time > STALE_AFTER_S:
                    logger.warning(
                        "user_data_ws: no frame for %ds — reconnecting",
                        STALE_AFTER_S,
                    )
                    return

    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass
