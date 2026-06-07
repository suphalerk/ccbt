"""Tests for the forward-test cohort classifier + manifest integrity."""

import json
from pathlib import Path

from research.forward_test_report import classify

MIN, GRAD = 15, 1.3


def test_under_gate_keeps_testing():
    assert classify(0, 0.0, MIN, GRAD) == "KEEP_TESTING"
    assert classify(14, 9.9, MIN, GRAD) == "KEEP_TESTING"  # great PF but too few trades


def test_graduates_when_gate_met_and_pf_good():
    assert classify(15, 1.3, MIN, GRAD) == "READY_TO_AUDIT"
    assert classify(30, 2.0, MIN, GRAD) == "READY_TO_AUDIT"


def test_drop_when_no_edge_after_gate():
    assert classify(20, 0.8, MIN, GRAD) == "DROP"


def test_marginal_between_1_and_graduate():
    assert classify(18, 1.1, MIN, GRAD) == "MARGINAL"


def test_manifest_valid_and_4h():
    m = json.loads((Path(__file__).resolve().parent.parent / "research" / "forward_test_cohort.json").read_text())
    assert m["min_trades"] == 15
    for c in m["candidates"]:
        assert c["tf"] == "4h"
        assert c["status"] in ("testing", "blocked_slot")
        assert c["config"].endswith("awesome4h.json")


def test_testing_configs_exist_and_are_4h():
    repo = Path(__file__).resolve().parent.parent
    m = json.loads((repo / "research" / "forward_test_cohort.json").read_text())
    for c in m["candidates"]:
        if c["status"] != "testing":
            continue
        cfg = json.loads((repo / c["config"]).read_text())
        assert cfg["timeframe_signal"] == "4h"
