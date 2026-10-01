from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Offline statistical analysis of Crash round history")
    p.add_argument("input", type=Path)
    p.add_argument("--column", default="crashpoint")
    p.add_argument("--threshold", type=float, default=2.0)
    p.add_argument("--permutations", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def load_series(path: Path, column: str) -> pd.Series:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path)
    elif suffix in {".jsonl", ".ndjson"}:
        df = pd.read_json(path, lines=True)
    elif suffix == ".json":
        df = pd.read_json(path)
    else:
        raise ValueError("Supported input formats: .csv, .jsonl/.ndjson, .json")

    if column not in df.columns:
        raise KeyError(f"Column {column!r} not found. Available: {list(df.columns)}")

    x = pd.to_numeric(df[column], errors="coerce").dropna()
    x = x[np.isfinite(x)]
    x = x[x >= 1.0].reset_index(drop=True)
    if len(x) < 30:
        raise ValueError(f"Need at least 30 valid rounds; found {len(x)}")
    return x.astype(float)


def lag_corr(x: np.ndarray, lag: int) -> float | None:
    if len(x) <= lag + 2:
        return None
    a = x[:-lag]
    b = x[lag:]
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def lag1_permutation_test(x: np.ndarray, permutations: int, seed: int) -> dict[str, float | int | None]:
    observed = lag_corr(x, 1)
    if observed is None:
        return {"observed": None, "p_value_two_sided": None, "permutations": permutations}

    rng = np.random.default_rng(seed)
    exceed = 0
    for _ in range(permutations):
        shuffled = rng.permutation(x)
        value = lag_corr(shuffled, 1)
        if value is not None and abs(value) >= abs(observed):
            exceed += 1

    return {
        "observed": observed,
        "p_value_two_sided": float((exceed + 1) / (permutations + 1)),
        "permutations": permutations,
    }


def runs_test(x: np.ndarray, threshold: float) -> dict[str, float | int | None]:
    bits = (x >= threshold).astype(np.int8)
    n1 = int(bits.sum())
    n0 = int(len(bits) - n1)

    if n0 == 0 or n1 == 0:
        return {
            "threshold": threshold,
            "runs": None,
            "z_score": None,
            "p_value_two_sided": None,
            "above_or_equal": n1,
            "below": n0,
        }

    runs = 1 + int(np.sum(bits[1:] != bits[:-1]))
    n = n0 + n1
    expected = 1.0 + (2.0 * n0 * n1) / n
    variance = (
        2.0 * n0 * n1 * (2.0 * n0 * n1 - n)
        / (n * n * (n - 1))
    )

    if variance <= 0:
        z = None
        p = None
    else:
        z = float((runs - expected) / np.sqrt(variance))
        p = float(2.0 * norm.sf(abs(z)))

    return {
        "threshold": threshold,
        "runs": runs,
        "expected_runs": expected,
        "z_score": z,
        "p_value_two_sided": p,
        "above_or_equal": n1,
        "below": n0,
    }


def longest_streak(mask: np.ndarray) -> int:
    best = 0
    current = 0
    for value in mask:
        if bool(value):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def main() -> None:
    args = parse_args()
    series = load_series(args.input, args.column)
    x = series.to_numpy(dtype=float)

    thresholds = [1.2, 1.5, 2.0, 3.0, 5.0, 10.0]
    empirical = {
        str(t): float(np.mean(x >= t))
        for t in thresholds
    }

    report = {
        "input": str(args.input),
        "column": args.column,
        "rounds": int(len(x)),
        "summary": {
            "mean": float(np.mean(x)),
            "median": float(np.median(x)),
            "std": float(np.std(x, ddof=1)),
            "min": float(np.min(x)),
            "max": float(np.max(x)),
            "quantiles": {
                "p01": float(np.quantile(x, 0.01)),
                "p05": float(np.quantile(x, 0.05)),
                "p25": float(np.quantile(x, 0.25)),
                "p50": float(np.quantile(x, 0.50)),
                "p75": float(np.quantile(x, 0.75)),
                "p95": float(np.quantile(x, 0.95)),
                "p99": float(np.quantile(x, 0.99)),
            },
        },
        "empirical_probability_ge": empirical,
        "serial_correlation": {
            str(lag): lag_corr(x, lag)
            for lag in (1, 2, 3, 5, 10, 25)
        },
        "lag1_permutation_test": lag1_permutation_test(
            x,
            permutations=max(100, args.permutations),
            seed=args.seed,
        ),
        "runs_test": runs_test(x, args.threshold),
        "streaks": {
            "threshold": args.threshold,
            "longest_below": longest_streak(x < args.threshold),
            "longest_above_or_equal": longest_streak(x >= args.threshold),
        },
    }

    p_perm = report["lag1_permutation_test"]["p_value_two_sided"]
    p_runs = report["runs_test"]["p_value_two_sided"]
    report["interpretation"] = {
        "lag1_dependence_detected_at_1pct": bool(p_perm is not None and p_perm < 0.01),
        "runs_nonrandom_at_1pct": bool(p_runs is not None and p_runs < 0.01),
        "note": (
            "Statistical significance in one historical sample is not a profit guarantee. "
            "Any apparent dependence should be replicated on fresh future data."
        ),
    }

    out_dir = Path(__file__).resolve().parent / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "analysis_report.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\nSaved: {output}")


if __name__ == "__main__":
    main()
