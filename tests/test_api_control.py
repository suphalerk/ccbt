"""N8 TDD — Bot mode control endpoints + auth + sym_clean parity.

Tests are written FIRST per the TDD hard rule.

Uses FastAPI dependency_overrides (same pattern as test_api_rest.py) so that
no importlib.reload() calls pollute module-level state for subsequent tests.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient

import api.deps as deps_module
from api.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_with_data_dir(tmp_path: Path, token: str = "") -> TestClient:
    """Build a TestClient whose get_db_path, verify_token, and data_dir
    are overridden to use tmp_path — no importlib.reload() needed.
    """
    data_dir = str(tmp_path)

    # Override get_db_path
    app.dependency_overrides[deps_module.get_db_path] = lambda: str(
        tmp_path / "trades.db"
    )

    # Override verify_token to simulate auth with the given token
    if token:
        # Token set: only the matching header passes
        async def _check_token(
            x_dash_token: Optional[str] = None,
        ) -> None:
            from fastapi import Header, HTTPException
            pass  # will be overridden below

        from fastapi import Header, HTTPException
        _expected = token

        async def _verify(
            x_dash_token: Optional[str] = None,
        ) -> None:
            pass  # dependency override — real logic inline below

        # We can't easily inject header checks with overrides alone, so we patch
        # the module-level CCBT_DASH_TOKEN when needed in individual tests.
        pass

    return TestClient(app, raise_server_exceptions=True)


def _reset_overrides() -> None:
    app.dependency_overrides.clear()


def _base_client(tmp_path: Path) -> TestClient:
    """Client with no auth required (CCBT_DASH_TOKEN unset in module)."""
    app.dependency_overrides[deps_module.get_db_path] = lambda: str(
        tmp_path / "trades.db"
    )
    # No auth override: the default verify_token uses deps_module.CCBT_DASH_TOKEN
    # which is None when the env var is not set.
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 1. sym_clean — lives in bot/mode.py (single source of truth)
# ---------------------------------------------------------------------------

class TestSymClean:
    """bot/mode.sym_clean must equal engine's inline replace for every roster symbol."""

    def test_sym_clean_imported_from_mode(self):
        from bot.mode import sym_clean
        assert callable(sym_clean)

    def test_parity_with_engine_inline(self):
        """sym_clean(s) == s.replace('/', '').replace(':', '') for all roster symbols."""
        import glob
        import re
        from bot.mode import sym_clean

        configs = glob.glob(str(REPO / "config_*.json"))
        assert configs, "No config_*.json files found"

        pattern = re.compile(r"^[A-Z0-9]{2,20}$")
        tested = 0
        for cfg_path in configs:
            try:
                cfg = json.loads(Path(cfg_path).read_text())
            except Exception:
                continue
            symbol = cfg.get("symbol", "")
            if not symbol:
                continue
            # Engine's inline formula (what engine.py:410/1593 does)
            engine_clean = symbol.replace("/", "").replace(":", "")
            try:
                api_clean = sym_clean(symbol)
                assert api_clean == engine_clean, (
                    f"{cfg_path}: sym_clean({symbol!r})={api_clean!r} "
                    f"!= engine inline {engine_clean!r}"
                )
                tested += 1
            except ValueError:
                # sym_clean raises for symbols failing regex (e.g. XAU_USD)
                # The engine inline does NOT raise — verify engine_clean also fails.
                assert not pattern.match(engine_clean), (
                    f"sym_clean({symbol!r}) raised but engine clean {engine_clean!r} "
                    f"matches regex — parity broken"
                )

        assert tested > 0, "No valid symbols tested"

    def test_sym_clean_strips_slash_colon(self):
        from bot.mode import sym_clean
        assert sym_clean("BTC/USDT:USDT") == "BTCUSDTUSDT"

    def test_sym_clean_plain_symbol(self):
        from bot.mode import sym_clean
        assert sym_clean("BTCUSDT") == "BTCUSDT"

    def test_sym_clean_rejects_dotdot(self):
        from bot.mode import sym_clean
        with pytest.raises(ValueError):
            sym_clean("../../etc/passwd")

    def test_sym_clean_rejects_percent_encoded(self):
        from bot.mode import sym_clean
        with pytest.raises(ValueError):
            sym_clean("%2e%2e%2fetc%2fpasswd")

    def test_sym_clean_rejects_lowercase(self):
        from bot.mode import sym_clean
        # sym_clean mirrors engine.py (no .upper()); the regex enforces uppercase,
        # so lowercase input must raise ValueError, not silently uppercase it.
        # Config symbols are always uppercase (engine never needs .upper() either).
        with pytest.raises(ValueError, match="does not match"):
            sym_clean("btcusdt")

    def test_sym_clean_rejects_too_short(self):
        from bot.mode import sym_clean
        with pytest.raises(ValueError):
            sym_clean("A")  # 1 char < min 2

    def test_sym_clean_rejects_too_long(self):
        from bot.mode import sym_clean
        with pytest.raises(ValueError):
            sym_clean("A" * 21)  # 21 chars > max 20


# ---------------------------------------------------------------------------
# 2. Roster loading
# ---------------------------------------------------------------------------

class TestRoster:
    def test_get_roster_returns_frozenset(self):
        from api.deps import get_roster
        roster = get_roster()
        assert isinstance(roster, frozenset)

    def test_roster_contains_deployed_symbols(self):
        from api.deps import get_roster
        roster = get_roster()
        # Roster must have at least one symbol
        assert len(roster) > 0

    def test_roster_no_invalid_symbols(self):
        """Every symbol in the roster must pass sym_clean without raising."""
        import re
        from api.deps import get_roster
        pattern = re.compile(r"^[A-Z0-9]{2,20}$")
        roster = get_roster()
        for sym in roster:
            assert pattern.match(sym), f"Invalid roster symbol: {sym!r}"

    def test_roster_imported_from_deps(self):
        """get_roster is exported from api.deps."""
        import api.deps
        assert hasattr(api.deps, "get_roster")


# ---------------------------------------------------------------------------
# 3. POST /api/bots/{symbol}/mode — happy path
# ---------------------------------------------------------------------------

class TestSetBotMode:
    def setup_method(self):
        _reset_overrides()

    def teardown_method(self):
        _reset_overrides()

    def test_valid_mode_write_creates_file(self, tmp_path):
        """Valid mode POST must write the correct mode_{sym}.json in data_dir."""
        from api.deps import get_roster
        from api.routers.control import _get_data_dir

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")
        sym = next(iter(roster))

        # Patch _get_data_dir to use tmp_path
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/bots/{sym}/mode",
                json={"mode": "graceful_stop"},
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["accepted"] is True
        assert data["mode"].lower() == "graceful_stop"

        mode_file = tmp_path / f"mode_{sym}.json"
        assert mode_file.exists(), f"Mode file not created: {mode_file}"
        payload = json.loads(mode_file.read_text())
        assert payload["mode"] == "graceful_stop"

    def test_valid_modes_accepted(self, tmp_path):
        """All four valid modes must be accepted."""
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")
        sym = next(iter(roster))

        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
            client = TestClient(app)

            for mode in ["normal", "graceful_stop", "tp_only", "panic"]:
                resp = client.post(f"/api/bots/{sym}/mode", json={"mode": mode})
                assert resp.status_code == 200, f"mode {mode!r} failed: {resp.text}"
                assert resp.json()["accepted"] is True


# ---------------------------------------------------------------------------
# 4. Invalid mode → 422
# ---------------------------------------------------------------------------

class TestInvalidMode:
    def setup_method(self):
        _reset_overrides()

    def teardown_method(self):
        _reset_overrides()

    def test_invalid_mode_422(self, tmp_path):
        """Unknown mode string must return 422 Unprocessable Entity."""
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")
        sym = next(iter(roster))

        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
            client = TestClient(app)

            resp = client.post(f"/api/bots/{sym}/mode", json={"mode": "turbo_yolo"})
            assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 5. Malicious / unknown symbol → 400, no file written
# ---------------------------------------------------------------------------

class TestSymbolValidation:
    def setup_method(self):
        _reset_overrides()

    def teardown_method(self):
        _reset_overrides()

    def _get_client(self, tmp_path: Path) -> TestClient:
        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        return TestClient(app, raise_server_exceptions=False)

    def test_path_traversal_dotdot_400(self, tmp_path):
        """Path-traversal attempts must not write any file and must not succeed."""
        client = self._get_client(tmp_path)
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            resp = client.post(
                "/api/bots/..%2F..%2Fetc%2Fpasswd/mode", json={"mode": "normal"}
            )
        # 400 (bad symbol), 404 (no route), 405 (wrong method), 422 — all prevent write
        assert resp.status_code in (400, 404, 405, 422), resp.text
        # No file should be written outside tmp_path
        for f in tmp_path.iterdir():
            assert "etc" not in str(f), f"Path traversal wrote {f}"

    def test_percent_encoded_slash_400(self, tmp_path):
        client = self._get_client(tmp_path)
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            resp = client.post("/api/bots/%2e%2e%2f/mode", json={"mode": "normal"})
        assert resp.status_code in (400, 404, 405, 422), resp.text

    def test_symbol_not_in_roster_400(self, tmp_path):
        """A valid-looking symbol not in the roster must be rejected."""
        client = self._get_client(tmp_path)
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            resp = client.post("/api/bots/ZZZNOTTHERE123/mode", json={"mode": "normal"})
        assert resp.status_code == 400, resp.text
        assert not (tmp_path / "mode_ZZZNOTTHERE123.json").exists()

    def test_no_file_written_for_rejected_symbol(self, tmp_path):
        """Rejected requests must NOT write any mode file."""
        client = self._get_client(tmp_path)
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client.post("/api/bots/ZZZNOTTHERE123/mode", json={"mode": "normal"})
        files = list(tmp_path.glob("mode_*.json"))
        assert files == [], f"Unexpected mode files: {files}"


# ---------------------------------------------------------------------------
# 6. Auth: POST without token → 401 when token required
# ---------------------------------------------------------------------------

class TestAuth:
    def setup_method(self):
        _reset_overrides()

    def teardown_method(self):
        _reset_overrides()
        # Restore the module-level token to its original value
        deps_module.CCBT_DASH_TOKEN = os.getenv("CCBT_DASH_TOKEN")

    def _require_token(self, secret: str) -> None:
        """Patch the module-level token so verify_token enforces auth."""
        deps_module.CCBT_DASH_TOKEN = secret

    def _clear_token(self) -> None:
        deps_module.CCBT_DASH_TOKEN = None

    def test_missing_token_401(self, tmp_path):
        """When CCBT_DASH_TOKEN is set, missing header must return 401."""
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")
        sym = next(iter(roster))

        self._require_token("secret123")
        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client = TestClient(app)
            resp = client.post(f"/api/bots/{sym}/mode", json={"mode": "normal"})

        assert resp.status_code == 401, resp.text

    def test_wrong_token_401(self, tmp_path):
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")
        sym = next(iter(roster))

        self._require_token("secret123")
        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client = TestClient(app)
            resp = client.post(
                f"/api/bots/{sym}/mode",
                json={"mode": "normal"},
                headers={"X-Dash-Token": "wrong"},
            )

        assert resp.status_code == 401, resp.text

    def test_correct_token_200(self, tmp_path):
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")
        sym = next(iter(roster))

        self._require_token("secret123")
        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client = TestClient(app)
            resp = client.post(
                f"/api/bots/{sym}/mode",
                json={"mode": "normal"},
                headers={"X-Dash-Token": "secret123"},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["accepted"] is True

    def test_no_token_env_no_auth_required(self, tmp_path):
        """When CCBT_DASH_TOKEN is None, no auth header should be needed (local dev)."""
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")
        sym = next(iter(roster))

        self._clear_token()
        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client = TestClient(app)
            resp = client.post(f"/api/bots/{sym}/mode", json={"mode": "normal"})

        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 7. Bulk mode endpoint
# ---------------------------------------------------------------------------

class TestBulkMode:
    def setup_method(self):
        _reset_overrides()
        # Reset PANIC debounce before each test
        import api.routers.control as ctrl
        ctrl._last_bulk_panic_ts = 0.0

    def teardown_method(self):
        _reset_overrides()
        import api.routers.control as ctrl
        ctrl._last_bulk_panic_ts = 0.0

    def test_bulk_applies_to_roster_only(self, tmp_path):
        """Bulk mode: non-roster symbols must be rejected."""
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")

        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client = TestClient(app)
            symbols = list(roster)[:3] + ["ZZZNOTTHERE123"]
            resp = client.post(
                "/api/bots/mode/bulk",
                json={"symbols": symbols, "mode": "graceful_stop"},
            )

        assert resp.status_code == 200, resp.text
        results = resp.json()["results"]
        by_sym = {r["symbol"]: r for r in results}

        for sym in list(roster)[:3]:
            if sym in by_sym:
                assert by_sym[sym]["accepted"] is True, f"{sym} should be accepted"
        if "ZZZNOTTHERE123" in by_sym:
            assert by_sym["ZZZNOTTHERE123"]["accepted"] is False

    def test_bulk_writes_mode_files(self, tmp_path):
        """Bulk mode write must create mode files for each accepted symbol."""
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")
        symbols = list(roster)[:2]

        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client = TestClient(app)
            resp = client.post(
                "/api/bots/mode/bulk",
                json={"symbols": symbols, "mode": "tp_only"},
            )

        assert resp.status_code == 200

        for sym in symbols:
            mode_file = tmp_path / f"mode_{sym}.json"
            assert mode_file.exists(), f"No mode file for {sym}"
            payload = json.loads(mode_file.read_text())
            assert payload["mode"] == "tp_only"

    def test_bulk_all_roster_on_empty_symbols(self, tmp_path):
        """Empty symbols list applies mode to ALL roster bots."""
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")

        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client = TestClient(app)
            resp = client.post(
                "/api/bots/mode/bulk",
                json={"symbols": [], "mode": "normal"},
            )

        assert resp.status_code == 200, resp.text
        results = resp.json()["results"]
        result_syms = {r["symbol"] for r in results}
        # All roster symbols should be in results
        for sym in roster:
            assert sym in result_syms, f"{sym} not in bulk result"


# ---------------------------------------------------------------------------
# 8. PANIC debounce — rapid 2nd bulk PANIC → 429
# ---------------------------------------------------------------------------

class TestPanicDebounce:
    def setup_method(self):
        _reset_overrides()
        import api.routers.control as ctrl
        ctrl._last_bulk_panic_ts = 0.0

    def teardown_method(self):
        _reset_overrides()
        import api.routers.control as ctrl
        ctrl._last_bulk_panic_ts = 0.0

    def test_rapid_second_panic_429(self, tmp_path):
        """A 2nd bulk PANIC within the debounce window must return 429."""
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")

        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client = TestClient(app)

            # First PANIC — must succeed
            resp1 = client.post(
                "/api/bots/mode/bulk",
                json={"symbols": [], "mode": "panic"},
            )
            assert resp1.status_code == 200, f"First PANIC failed: {resp1.text}"

            # Immediate 2nd PANIC — must be debounced (429)
            resp2 = client.post(
                "/api/bots/mode/bulk",
                json={"symbols": [], "mode": "panic"},
            )
            assert resp2.status_code == 429, (
                f"Expected 429 debounce, got {resp2.status_code}: {resp2.text}"
            )

    def test_single_bot_panic_not_debounced(self, tmp_path):
        """Single-bot PANIC is NOT subject to bulk PANIC debounce."""
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")
        sym = next(iter(roster))

        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client = TestClient(app)

            # Two single-bot PANICs back-to-back — both must succeed
            resp1 = client.post(f"/api/bots/{sym}/mode", json={"mode": "panic"})
            assert resp1.status_code == 200, resp1.text
            resp2 = client.post(f"/api/bots/{sym}/mode", json={"mode": "panic"})
            assert resp2.status_code == 200, resp2.text

    def test_debounce_resets_for_non_panic(self, tmp_path):
        """After debounce, a non-panic bulk request still works."""
        from api.deps import get_roster

        roster = get_roster()
        if not roster:
            pytest.skip("No roster symbols found")

        app.dependency_overrides[deps_module.get_db_path] = lambda: ":memory:"
        with patch("api.routers.control._get_data_dir", return_value=str(tmp_path)):
            client = TestClient(app)

            # Trigger debounce
            client.post("/api/bots/mode/bulk", json={"symbols": [], "mode": "panic"})

            # Normal mode should still work (debounce only blocks PANIC)
            resp = client.post(
                "/api/bots/mode/bulk",
                json={"symbols": [], "mode": "normal"},
            )
            assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 9. Startup assertion: refuse to start if host != 127.0.0.1 and token unset
# ---------------------------------------------------------------------------

class TestStartupAssertion:
    def test_startup_assertion_function_exists(self):
        """assert_startup_safety must be importable from api.deps."""
        from api.deps import assert_startup_safety
        assert callable(assert_startup_safety)

    def test_startup_safe_localhost_no_token(self):
        """localhost + no token is safe (local dev)."""
        from api.deps import assert_startup_safety
        assert_startup_safety(host="127.0.0.1", token=None)

    def test_startup_safe_localhost_with_token(self):
        from api.deps import assert_startup_safety
        assert_startup_safety(host="127.0.0.1", token="secret")

    def test_startup_safe_nonlocal_with_token(self):
        from api.deps import assert_startup_safety
        assert_startup_safety(host="0.0.0.0", token="secret")

    def test_startup_unsafe_nonlocal_no_token(self):
        """Non-localhost without a token must raise RuntimeError."""
        from api.deps import assert_startup_safety
        with pytest.raises(RuntimeError, match="CCBT_DASH_TOKEN"):
            assert_startup_safety(host="0.0.0.0", token=None)

    def test_startup_unsafe_nonlocal_empty_token(self):
        from api.deps import assert_startup_safety
        with pytest.raises(RuntimeError, match="CCBT_DASH_TOKEN"):
            assert_startup_safety(host="0.0.0.0", token="")

    def test_startup_safe_ipv6_loopback(self):
        from api.deps import assert_startup_safety
        assert_startup_safety(host="::1", token=None)

    def test_startup_safe_localhost_string(self):
        from api.deps import assert_startup_safety
        assert_startup_safety(host="localhost", token=None)


# ---------------------------------------------------------------------------
# 10. sym_clean round-trip: API mode-file path == engine mode-file path
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_mode_file_path_parity(self):
        """The mode-file path written by the API must equal what the engine reads.

        Engine formula: data_dir / f'mode_{symbol.replace("/","").replace(":","")}.json'
        API formula:    data_dir / f'mode_{sym_clean(symbol)}.json'
        These MUST be the same file for every roster symbol.
        """
        import glob
        from bot.mode import sym_clean

        configs = glob.glob(str(REPO / "config_*.json"))
        tested = 0
        for cfg_path in configs[:20]:  # First 20 for speed
            try:
                cfg = json.loads(Path(cfg_path).read_text())
            except Exception:
                continue
            symbol = cfg.get("symbol", "")
            if not symbol:
                continue

            engine_clean = symbol.replace("/", "").replace(":", "")
            try:
                api_clean = sym_clean(symbol)
            except ValueError:
                continue  # OANDA XAU_USD etc.

            assert api_clean == engine_clean, (
                f"Path divergence for {symbol!r}: "
                f"api={api_clean!r}, engine={engine_clean!r}"
            )
            tested += 1

        assert tested > 0, "No valid symbols tested"
