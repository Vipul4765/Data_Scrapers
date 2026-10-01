# Stake Crash Offline Analysis

This folder is for **offline statistical analysis** of Crash round-history data.

It does not place bets, automate gambling, bypass site protections, or produce betting signals.

## What it checks

- crash-multiplier distribution
- empirical probabilities above common thresholds
- quantiles and tail behavior
- lag correlations
- permutation test for lag-1 dependence
- runs test around a selected threshold
- longest above/below-threshold streaks

The purpose is to test whether historical data shows reproducible dependence instead of assuming that streaks are predictive.

## Input

Provide a CSV or JSONL file containing a numeric `crashpoint` column.

Example:

```text
crashpoint
1.04
2.31
1.22
8.10
```

## Install

```powershell
cd stake\crash_analysis
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python analyze.py path\to\rounds.csv
```

Optional:

```powershell
python analyze.py path\to\rounds.csv --column crashpoint --threshold 2.0 --permutations 5000
```

The report is written to `artifacts/analysis_report.json`.
