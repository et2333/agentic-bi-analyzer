"""SRD Tool -- Success-Rate Degradation
---------------------------------------
Measures how robustly the pipeline handles dirty / noisy data compared with
clean data.  Two sub-commands:

  prepare_dirty   Inject realistic noise into a clean CSV
  srd             Compute SR_clean, SR_dirty, and SRD from existing run dirs

Typical workflow
----------------
  # Step 1 -- create a dirty version of the split train CSV
  python srd_tool.py prepare_dirty \\
      --clean  datasets/bank_churn/split_train.csv \\
      --output datasets/bank_churn/split_train_dirty.csv \\
      --target Exited

  # Step 2 -- run main.py once on clean data, once on dirty data.
  #           You get two run-dirs, e.g.:
  #             clean: artifacts/workspace/2026-05-20-10-00
  #             dirty: artifacts/workspace/2026-05-20-11-00

  # Step 3 -- compute SRD (no re-running, reads state snapshots only)
  python srd_tool.py srd \\
      --clean-dirs "artifacts/workspace/2026-05-20-10-00" \\
      --dirty-dirs "artifacts/workspace/2026-05-20-11-00"

  # With multiple runs per condition (better statistical estimate):
  python srd_tool.py srd \\
      --clean-dirs "run_c1,run_c2,run_c3" \\
      --dirty-dirs "run_d1,run_d2,run_d3"

Notes
-----
- A run is "successful" (SR=1) if *_state.json contains a non-empty best_model.
- prepare_dirty never modifies the target column.
- Results are appended to srd_records.jsonl for longitudinal tracking.
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

_ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts" / "workspace"
_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

RECORDS_FILE = str(_ARTIFACTS_DIR / "srd_records.jsonl")

# ---- Helpers -----------------------------------------------------------------

def _find_state_snapshot(run_dir: str) -> dict:
    reports = Path(run_dir) / "reports"
    snaps   = sorted(reports.glob("*_state.json"))
    if not snaps:
        raise FileNotFoundError(f"No *_state.json in {reports}")
    with open(snaps[-1], "r", encoding="utf-8") as f:
        return json.load(f)


def _run_succeeded(run_dir: str) -> bool:
    """True if the run produced a non-empty best_model."""
    try:
        state = _find_state_snapshot(run_dir)
        bm    = state.get("best_model")
        return bool(bm and bm.get("model_name"))
    except Exception:
        return False


def _sr_from_dirs(dirs: list[str]) -> tuple[float, int, int]:
    """Return (SR, successes c, total n)."""
    n  = len(dirs)
    c  = sum(1 for d in dirs if _run_succeeded(d))
    sr = round(c / n, 4) if n else 0.0
    return sr, c, n


# ---- prepare_dirty -----------------------------------------------------------

def cmd_prepare_dirty(args: argparse.Namespace) -> None:
    """
    Inject three types of realistic noise into a clean CSV:

      1. Random missing values   (--missing-rate,      default 0.05)
         Randomly NaN cells across all non-target columns.

      2. Numeric outliers         (--outlier-rate,      default 0.03)
         Multiply a fraction of numeric cells by 10x (simulates sensor /
         data-entry error common in real production exports).

      3. Type corruption          (--type-corrupt-rate, default 0.02)
         Replace a fraction of numeric cells with the string "N/A",
         forcing the system to handle mixed-type columns gracefully.

    The --target column is never touched so label integrity is preserved.
    """
    rng    = np.random.default_rng(args.seed)
    df     = pd.read_csv(args.clean)
    target = (args.target or "").strip()

    feature_cols = [c for c in df.columns if c != target]
    numeric_cols = df[feature_cols].select_dtypes(include="number").columns.tolist()
    n_rows       = len(df)

    # 1. Missing values
    for col in feature_cols:
        mask = rng.random(n_rows) < args.missing_rate
        df.loc[mask, col] = np.nan

    # 2. Numeric outliers (10x multiplier)
    for col in numeric_cols:
        mask = rng.random(n_rows) < args.outlier_rate
        df.loc[mask, col] = df.loc[mask, col] * 10

    # 3. Type corruption -- inject "N/A" string into numeric columns
    if args.type_corrupt_rate > 0:
        for col in numeric_cols:
            mask = rng.random(n_rows) < args.type_corrupt_rate
            df[col] = df[col].astype(object)
            df.loc[mask, col] = "N/A"

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    total_cells = n_rows * len(feature_cols)
    nan_count   = int(df[feature_cols].isna().sum().sum())
    SEP = "-" * 50
    print(f"\n  [prepare_dirty]  {out}")
    print(SEP)
    print(f"  Rows              : {n_rows}")
    print(f"  Feature cols      : {len(feature_cols)}  "
          f"(target '{target}' untouched)")
    print(f"  NaN injected      : {nan_count} / {total_cells} "
          f"({100 * nan_count / total_cells:.1f}%)")
    print(f"  Outlier rate      : {args.outlier_rate}")
    print(f"  Type-corrupt rate : {args.type_corrupt_rate}")
    print(f"  Random seed       : {args.seed}")
    print(SEP)


# ---- srd ---------------------------------------------------------------------

def cmd_srd(args: argparse.Namespace) -> None:
    """
    Compute Success-Rate Degradation.

      SR_clean  = fraction of clean-data runs that produced a best_model
      SR_dirty  = fraction of dirty-data runs that produced a best_model
      SRD       = SR_clean - SR_dirty

    Robustness tiers:
      SRD <= 0.05                -> ROBUST
      0.05 < SRD <= 0.20         -> MODERATE degradation
      SRD >  0.20                -> HIGH degradation
    """
    clean_dirs = [d.strip() for d in args.clean_dirs.split(",") if d.strip()]
    dirty_dirs = [d.strip() for d in args.dirty_dirs.split(",") if d.strip()]

    if not clean_dirs or not dirty_dirs:
        raise ValueError("Both --clean-dirs and --dirty-dirs must be non-empty.")

    print("\n  Checking clean runs ...")
    clean_detail = []
    for d in clean_dirs:
        ok = _run_succeeded(d)
        print(f"    {'OK  ' if ok else 'FAIL'}  {d}")
        clean_detail.append({"run_dir": d, "success": ok})

    print("  Checking dirty runs ...")
    dirty_detail = []
    for d in dirty_dirs:
        ok = _run_succeeded(d)
        print(f"    {'OK  ' if ok else 'FAIL'}  {d}")
        dirty_detail.append({"run_dir": d, "success": ok})

    sr_clean, c_clean, n_clean = _sr_from_dirs(clean_dirs)
    sr_dirty, c_dirty, n_dirty = _sr_from_dirs(dirty_dirs)
    srd = round(sr_clean - sr_dirty, 4)

    if srd <= 0.05:
        tier = "ROBUST  (SRD <= 0.05)"
    elif srd <= 0.20:
        tier = "MODERATE degradation  (0.05 < SRD <= 0.20)"
    else:
        tier = "HIGH degradation  (SRD > 0.20)"

    SEP = "=" * 58
    print(f"\n{SEP}")
    print("  SRD -- SUCCESS-RATE DEGRADATION")
    print(SEP)
    print(f"  {'Clean runs':<20}: {c_clean}/{n_clean}  SR_clean = {sr_clean}")
    print(f"  {'Dirty runs':<20}: {c_dirty}/{n_dirty}  SR_dirty = {sr_dirty}")
    print(f"  {'SRD':<20}: {srd}  (SR_clean - SR_dirty)")
    print(f"  {'Assessment':<20}: {tier}")
    print(SEP)

    now     = datetime.now()
    case_id = now.strftime("%Y%m%d-%H%M%S") + "-srd"
    record  = {
        "case_id":   case_id,
        "timestamp": now.isoformat(timespec="seconds"),
        "system":    {"name": args.system_name, "type": args.system_type},
        "srd": {
            "SR_clean":   sr_clean,
            "n_clean":    n_clean,
            "c_clean":    c_clean,
            "SR_dirty":   sr_dirty,
            "n_dirty":    n_dirty,
            "c_dirty":    c_dirty,
            "SRD":        srd,
            "tier":       tier,
            "clean_runs": clean_detail,
            "dirty_runs": dirty_detail,
        },
    }
    with open(RECORDS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"\n  Record appended to: {RECORDS_FILE}  (case_id={case_id})\n")


# ---- CLI ---------------------------------------------------------------------

def _add_system_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--system-name", default="OurSystem", dest="system_name")
    p.add_argument("--system-type", default="multi-agent DS pipeline",
                   dest="system_type")


if __name__ == "__main__":
    root = argparse.ArgumentParser(
        description="SRD Tool -- Success-Rate Degradation for scalability evaluation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = root.add_subparsers(dest="command", required=True)

    # -- prepare_dirty ---------------------------------------------------------
    p_dirty = sub.add_parser(
        "prepare_dirty",
        help="Inject realistic noise into a CSV to create a dirty dataset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Noise types injected (all rates are per-cell probabilities):
  --missing-rate       Random NaN across non-target columns   (default 0.05)
  --outlier-rate       Numeric cells multiplied by 10x        (default 0.03)
  --type-corrupt-rate  Numeric cells replaced with "N/A"      (default 0.02)
""",
    )
    p_dirty.add_argument("--clean",  required=True,
                         help="Path to clean input CSV")
    p_dirty.add_argument("--output", required=True,
                         help="Path to write dirty CSV")
    p_dirty.add_argument("--target", default="",
                         help="Target column -- never modified")
    p_dirty.add_argument("--missing-rate",      type=float, default=0.05,
                         dest="missing_rate")
    p_dirty.add_argument("--outlier-rate",      type=float, default=0.03,
                         dest="outlier_rate")
    p_dirty.add_argument("--type-corrupt-rate", type=float, default=0.02,
                         dest="type_corrupt_rate")
    p_dirty.add_argument("--seed", type=int, default=42)

    # -- srd -------------------------------------------------------------------
    p_srd = sub.add_parser(
        "srd",
        help="Compute SR_clean, SR_dirty and SRD from existing run directories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Reads *_state.json from each run directory to determine success (best_model
present).  No re-running or extra LLM calls required.
""",
    )
    p_srd.add_argument(
        "--clean-dirs", required=True, dest="clean_dirs",
        help="Comma-separated run-dirs produced on clean data",
    )
    p_srd.add_argument(
        "--dirty-dirs", required=True, dest="dirty_dirs",
        help="Comma-separated run-dirs produced on dirty data",
    )
    _add_system_args(p_srd)

    # -- dispatch --------------------------------------------------------------
    args = root.parse_args()

    if args.command == "prepare_dirty":
        cmd_prepare_dirty(args)
    elif args.command == "srd":
        cmd_srd(args)
