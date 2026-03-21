"""One-off script: add strategy_name field to every deployed bot config.

Run from project root:
    python3 scripts/add_strategy_name.py
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Files to skip (non-bot / non-deployed)
SKIP_CONFIGS = {
    "config_aggressive.json",
    "config_sniper.json",
    "config_yolo.json",
    "config_max.json",
    "config_gold.json",
    "config_gold_forex.json",
}

# Explicit overrides take precedence over pattern matching
EXPLICIT_OVERRIDES: dict[str, str] = {
    "config.json": "EMA 15m",
    "config_wif.json": "EMA 15m",
    "config_avax_ichi.json": "Ichi 1H",
    # Upgraded configs: filename says ichi/ichi4h but actual strategy differs
    "config_near_ichi.json": "EMA+Ichi 4H",
    "config_zetausdt_ichi.json": "EMA+Ichi 4H",
    "config_hbarusdt_ichi4h.json": "Alligator 4H",
    "config_arcusdt_ichi.json": "Alligator 4H",
}

# Pattern-based detection (checked in order; first match wins)
STRATEGY_PATTERNS = [
    ("_ichi4htrail", "4H Trail"),
    ("_emaichi4h", "EMA+Ichi 4H"),
    ("_ichist4h", "Ichi+ST 4H"),
    ("_alligator4h", "Alligator 4H"),
    ("_alligator", "Alligator 1H"),
    ("_dualst4h", "Dual ST 4H"),
    ("_dualst", "Dual ST 1H"),
    ("_ichi4h", "Ichi 4H"),
    ("_ichi", "Ichi 1H"),
    ("_ema", "EMA 15m"),
    ("_supertrend", "Supertrend"),
    ("_volexp", "VolExp"),
]


def detect_strategy(stem: str) -> str:
    """Return strategy label from config filename stem."""
    for suffix, label in STRATEGY_PATTERNS:
        if stem.endswith(suffix):
            return label
    return "EMA 15m"  # bare 'config' is BTC EMA 15m; anything else unknown


def insert_after_comment(data: dict, strategy_name: str) -> dict:
    """Return a new ordered dict with strategy_name inserted after _comment (or first)."""
    new: dict = {}
    inserted = False
    for k, v in data.items():
        new[k] = v
        if k == "_comment" and not inserted:
            new["strategy_name"] = strategy_name
            inserted = True
    if not inserted:
        # No _comment key; insert as second key after first key
        result: dict = {}
        for i, (k, v) in enumerate(new.items()):
            result[k] = v
            if i == 0 and not inserted:
                result["strategy_name"] = strategy_name
                inserted = True
        new = result
    return new


def process_configs() -> None:
    configs = list(PROJECT_ROOT.glob("config*.json"))
    configs.sort()
    updated = 0
    skipped = 0

    for cfg_path in configs:
        name = cfg_path.name

        if name in SKIP_CONFIGS:
            print(f"  SKIP  {name}")
            skipped += 1
            continue

        try:
            with open(cfg_path) as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ERROR reading {name}: {e}")
            continue

        # Determine strategy
        if name in EXPLICIT_OVERRIDES:
            strategy_name = EXPLICIT_OVERRIDES[name]
        else:
            strategy_name = detect_strategy(cfg_path.stem)

        # Skip if already set to the correct value
        if data.get("strategy_name") == strategy_name:
            print(f"  OK    {name}  →  {strategy_name}")
            continue

        # Build updated dict with strategy_name in the right position
        new_data = insert_after_comment(data, strategy_name)

        with open(cfg_path, "w") as f:
            json.dump(new_data, f, indent=2)
            f.write("\n")

        action = "UPDATE" if "strategy_name" in data else "ADD"
        print(f"  {action}  {name}  →  {strategy_name}")
        updated += 1

    print(f"\nDone. {updated} configs updated, {skipped} skipped.")


if __name__ == "__main__":
    process_configs()
