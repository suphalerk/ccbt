#!/usr/bin/env python3
"""Apply a deployment profile to all bot configs.

Reads a profile from configs/profiles/{name}.json and overrides
risk/leverage/position settings in all active bot configs.

Usage:
    python scripts/apply_profile.py real-test     # apply real-test profile
    python scripts/apply_profile.py real-01        # apply real-01 profile
    python scripts/apply_profile.py testnet        # back to testnet
    python scripts/apply_profile.py --list         # show available profiles
    python scripts/apply_profile.py real-test --dry-run  # preview changes

The profile overrides these keys in every config:
    use_testnet, risk_per_trade, leverage, max_positions,
    max_daily_loss, max_consecutive_losses
"""

import argparse
import json
import glob
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = PROJECT_ROOT / "configs" / "profiles"

# Keys that profiles override in bot configs
OVERRIDE_KEYS = [
    "use_testnet", "risk_per_trade", "leverage",
    "max_positions", "max_daily_loss", "max_consecutive_losses",
]

# Configs to skip (not bots)
SKIP_CONFIGS = {
    "config_aggressive.json", "config_sniper.json", "config_yolo.json",
    "config_max.json", "config_gold.json", "config_gold_forex.json",
}

# Retired/replaced configs
SKIP_RETIRED = {
    "config_doge.json", "config_sol_ichi.json", "config_btc_ichi.json",
    "config_arb.json", "config_1000shibusdt_ichi.json",
    "config_trxusdt_ichi.json", "config_xlmusdt_ichi.json",
    "config_saharausdt_supertrend.json", "config_polusdt_ichi4htrail.json",
}


def list_profiles():
    """Print available profiles."""
    print("Available profiles:")
    print()
    for p in sorted(PROFILES_DIR.glob("*.json")):
        try:
            cfg = json.load(open(p))
            name = cfg.get("_name", p.stem)
            desc = cfg.get("_description", "")
            testnet = cfg.get("use_testnet", "?")
            risk = cfg.get("risk_per_trade", "?")
            lev = cfg.get("leverage", "?")
            maxp = cfg.get("max_positions", "?")
            print(f"  {name:<12} {'TESTNET' if testnet else 'MAINNET':>8}  risk={risk}  lev={lev}x  max_pos={maxp}")
            if desc:
                print(f"               {desc}")
            print()
        except Exception as e:
            print(f"  {p.stem:<12} ERROR: {e}")


def get_bot_configs(profile: dict) -> list[Path]:
    """Get list of config files to apply profile to."""
    # If profile has specific bots list, use only those
    bots = profile.get("bots")
    if bots:
        configs = []
        for b in bots:
            cfg_path = PROJECT_ROOT / b["config"]
            if cfg_path.exists():
                configs.append(cfg_path)
            else:
                print(f"  WARNING: {b['config']} not found, skipping")
        return configs

    # Otherwise apply to all bot configs
    all_configs = []
    for p in sorted(PROJECT_ROOT.glob("config*.json")):
        if p.name in SKIP_CONFIGS or p.name in SKIP_RETIRED:
            continue
        all_configs.append(p)
    return all_configs


def apply_profile(profile_name: str, dry_run: bool = False):
    """Apply a profile to bot configs."""
    profile_path = PROFILES_DIR / f"{profile_name}.json"
    if not profile_path.exists():
        print(f"ERROR: Profile '{profile_name}' not found at {profile_path}")
        print(f"Available: {', '.join(p.stem for p in PROFILES_DIR.glob('*.json'))}")
        sys.exit(1)

    profile = json.load(open(profile_path))
    name = profile.get("_name", profile_name)
    testnet = profile.get("use_testnet", True)
    mode = "TESTNET" if testnet else "MAINNET"

    print(f"{'[DRY RUN] ' if dry_run else ''}Applying profile: {name} ({mode})")
    print(f"  risk={profile.get('risk_per_trade')}  leverage={profile.get('leverage')}x  max_positions={profile.get('max_positions')}")
    print()

    # Safety confirmation for mainnet
    if not testnet and not dry_run:
        print(f"  ⚠️  This will switch configs to MAINNET (real money)!")
        confirm = input("  Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("  Aborted.")
            sys.exit(0)
        print()

    configs = get_bot_configs(profile)
    print(f"  Configs to update: {len(configs)}")

    changed = 0
    for cfg_path in configs:
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)

            modifications = []
            for key in OVERRIDE_KEYS:
                if key in profile and key in cfg:
                    old_val = cfg[key]
                    new_val = profile[key]
                    if old_val != new_val:
                        modifications.append(f"{key}: {old_val} → {new_val}")
                        if not dry_run:
                            cfg[key] = new_val

            if modifications:
                changed += 1
                if dry_run:
                    print(f"  WOULD change {cfg_path.name}:")
                    for m in modifications:
                        print(f"    {m}")
                else:
                    with open(cfg_path, "w") as f:
                        json.dump(cfg, f, indent=2, ensure_ascii=False)
                        f.write("\n")

        except Exception as e:
            print(f"  ERROR {cfg_path.name}: {e}")

    print()
    if dry_run:
        print(f"[DRY RUN] Would change {changed}/{len(configs)} configs")
    else:
        print(f"✅ Applied '{name}' to {changed}/{len(configs)} configs")
        print()
        print("Next steps:")
        if not testnet:
            print("  1. Restart bots: bash scripts/start_all_bots.sh")
            print("  2. Monitor Telegram for alerts")
            print("  3. Check dashboard: http://localhost:8501")
        else:
            print("  1. Restart bots: bash scripts/start_all_bots.sh")


def main():
    parser = argparse.ArgumentParser(
        description="Apply a deployment profile to all bot configs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("profile", nargs="?", help="Profile name (e.g. real-test, real-01, testnet)")
    parser.add_argument("--list", action="store_true", help="List available profiles")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args()

    if args.list or not args.profile:
        list_profiles()
        return

    apply_profile(args.profile, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
