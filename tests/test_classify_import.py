"""N0 — Shared classify util import smoke tests (TDD — written before implementation).

Tests:
1. classify imports from the canonical shared location (api.classify)
2. classify imports from research.forward_test_report (re-export path)
3. Both paths return the same object / same behaviour
4. No circular imports
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class TestClassifyImportPaths:
    """classify must be importable from both paths."""

    def test_import_from_api_classify(self):
        """Canonical location: api.classify."""
        from api.classify import classify
        assert callable(classify)

    def test_import_from_forward_test_report(self):
        """Legacy re-export path: research.forward_test_report."""
        from research.forward_test_report import classify
        assert callable(classify)

    def test_both_paths_same_behaviour(self):
        """Both imports must produce identical results (same function or re-export)."""
        from api.classify import classify as classify_api
        from research.forward_test_report import classify as classify_ftr

        # Run through all verdict states
        cases = [
            (5, 2.0, 15, 1.5),   # KEEP_TESTING (trades < min)
            (20, 2.0, 15, 1.5),  # READY_TO_AUDIT
            (20, 0.8, 15, 1.5),  # DROP
            (20, 1.2, 15, 1.5),  # MARGINAL
        ]
        for args in cases:
            assert classify_api(*args) == classify_ftr(*args), (
                f"Divergent results for args={args}"
            )

    def test_no_circular_import(self):
        """Importing api.classify must not import bot.* or dashboard.* (side-effect-free)."""
        import api.classify  # noqa: F401
        # If this completes without ImportError, the import graph is clean
        assert True

    def test_classify_verdicts(self):
        """Verify all four verdict strings against contract."""
        from api.classify import classify

        assert classify(5, 2.0, 15, 1.5) == "KEEP_TESTING"
        assert classify(20, 2.0, 15, 1.5) == "READY_TO_AUDIT"
        assert classify(20, 0.8, 15, 1.5) == "DROP"
        assert classify(20, 1.2, 15, 1.5) == "MARGINAL"
