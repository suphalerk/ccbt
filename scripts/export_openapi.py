#!/usr/bin/env python3
"""Export the FastAPI OpenAPI spec to openapi.json in the repo root.

Run after any change to api/models.py or api/routers/*.py to keep the
committed spec in sync with the app.  This file is the source of truth for
npm run generate-types in web/.

Usage:
    .venv-dash/bin/python scripts/export_openapi.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from api.main import app  # noqa: E402

spec = app.openapi()
out = REPO / "openapi.json"
out.write_text(json.dumps(spec, indent=2) + "\n")
print(f"Wrote {out} ({len(spec.get('paths', {}))} paths)")
