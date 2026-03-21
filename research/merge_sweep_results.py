"""Merge sweep result batches and print final sorted table."""
import glob, json, os, sys
import numpy as np
sys.path.insert(0, "/Users/iceai/Work/ccbt")

DATA_DIR = "/Users/iceai/Work/ccbt/data"

# Re-run all coins using the sweep script batched at 25 at a time
import subprocess, warnings
warnings.filterwarnings("ignore")

files = sorted(glob.glob(os.path.join(DATA_DIR, "*_1h_2y.csv")))
coins = [os.path.basename(f).replace("_1h_2y.csv", "") for f in files]
print(f"Total coins: {len(coins)}")

# Run in batches of 25
all_results = []
batch_size = 25
PYTHON = sys.executable

for i in range(0, len(coins), batch_size):
    batch = coins[i:i+batch_size]
    batch_str = ",".join(batch)
    print(f"\nBatch {i//batch_size + 1}: coins {i+1}-{i+len(batch)}")
    result = subprocess.run(
        [PYTHON, "/Users/iceai/Work/ccbt/research/sweep_new_strategies_1.py",
         "--min-pf", "0.0", "--min-trades", "4", "--coins", batch_str],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        print(f"  FAILED: {result.stderr[:200]}")
        continue

    # Load saved JSON
    json_path = os.path.join(DATA_DIR, "sweep_new_strats_1.json")
    with open(json_path) as f:
        batch_results = json.load(f)
    all_results.extend(batch_results)
    print(f"  Got {len(batch_results)} results")

# Save merged results
all_results.sort(key=lambda x: -x["pf"])
out_path = os.path.join(DATA_DIR, "sweep_new_strats_1_full.json")
with open(out_path, "w") as f:
    json.dump(all_results, f, indent=2)
print(f"\nTotal results: {len(all_results)}")
print(f"Saved to {out_path}")

# Print final table — PF >= 1.3, trades >= 6
winners = [r for r in all_results if r["pf"] >= 1.3 and r["trades"] >= 6]
winners.sort(key=lambda x: -x["pf"])

header = f"{'Coin':<12} {'Strategy':<24} {'PF':>6} {'WR%':>6} {'Trades':>7} {'Sharpe':>8}"
sep = "-" * len(header)
print(f"\nFINAL RESULTS: Strategies 1-10 across all {len(coins)} coins (2yr, 1H + 4H)")
print(f"Showing PF >= 1.3, trades >= 6")
print(header)
print(sep)
for r in winners:
    print(f"{r['coin']:<12} {r['strategy']:<24} {r['pf']:>6.2f} {r['wr_pct']:>5.1f}% {r['trades']:>7} {r['sharpe']:>8.2f}")
print(sep)
print(f"Total winners (PF>=1.3, T>=6): {len(winners)} / {len(all_results)}")

# Strategy summary
strat_pf = {}
for r in all_results:
    if r["trades"] >= 4:
        strat_pf.setdefault(r["strategy"], []).append(r["pf"])

print("\nStrategy summary (mean PF, sorted):")
rows = [(s, np.mean(pfs), sum(1 for p in pfs if p >= 1.3), len(pfs)) for s, pfs in strat_pf.items()]
rows.sort(key=lambda x: -x[1])
print(f"  {'Strategy':<24} {'AvgPF':>7} {'>=1.3':>6} {'Tested':>7}")
for row in rows:
    print(f"  {row[0]:<24} {row[1]:>7.2f} {row[2]:>6} {row[3]:>7}")
