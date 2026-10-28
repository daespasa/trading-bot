# ML methodology

Question: **can a model estimate P(YES) better than the Polymarket price?** If not, the AI wallets never trade.

## Data, all real

1. **history** (`data/ml/history.csv`, grows with each run): binary markets that Gamma lists as `closed` and `umaResolutionStatus=resolved`, with volume ≥ 5,000 and payouts exactly 1/0. Sub-hour crypto markets are excluded.
   For each market, observation times sit at 1, 3, 6, 12, 24, 48, 96 and 168 h before the **scheduled** end date. Only times before the market actually closed are kept.
   Features come from the CLOB `prices-history` series (hourly), using only points up to the observation time.
2. **live**: hourly samples of this lab's own snapshots for markets that later resolved. These add spread, book imbalance and depth. The `book` feature set trains only once enough of these exist.

The history price is a last-trade/mid series, not an executable quote. Any result on it is a probability-quality result. Profitability is judged separately with real asks in paper trading.

## Features (core)

`p`, `logit(p)`, price change over 1 h / 6 h / 24 h, 24 h volatility and range, log hours to the scheduled end, log market age, number of observations in the last 24 h, and category one-hots.
Volume is excluded because the API reports volume as of now, which would leak the future. `tests/test_ml.py` checks that changing the future does not change the features.

## Validation

- **Walk-forward, 4 folds** over the newest half of observation times. For a test window starting at time *a*, training uses only rows whose market **resolved before *a***, so every label was knowable at prediction time.
- **Calibration**: Platt scaling fitted on the most recent 20 % of each training set (by resolution time).
- Metrics on the pooled out-of-sample predictions: log loss, Brier score, expected calibration error, accuracy, precision and recall, plus calibration bins. The same metrics are computed for the market price as the baseline.
- **Significance**: the mean per-row log-loss improvement over the market, with a standard error clustered by market (rows of one market are correlated).
- **Simulated rule**: the first observation per market where |p_model − p| > 0.05. It buys at p + 0.01 half-spread + the category taker fee.

## Activation gate (all required)

- at least 600 rows, 120 markets, 300 out-of-sample rows and 60 out-of-sample markets, otherwise **INSUFFICIENT TRAINING DATA**
- log-loss gain over the market with clustered t > 2
- ECE < 0.05
- simulated rule ROI > 0 on at least 30 trades

Failing any of these gives **NO DEMONSTRATED PREDICTIVE EDGE**. The model still makes predictions, and they are stored and scored live as markets resolve (`/api/ml/live-metrics`), but its wallet does not trade.

## Resources

Training runs in a separate process (`python -m app.machine_learning.train`) at nice 10, with `OMP/OPENBLAS/MKL` threads and LightGBM `n_jobs` set to `ML_THREADS` (2 in the low profile). Only one run happens at a time.
It is postponed when free RAM is below 1.2 GB, and paused (SIGSTOP) below 0.6 GB until memory recovers. XGBoost is used automatically if you `pip install xgboost`.
