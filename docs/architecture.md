# Architecture

CryptoRadar OSS separates research into four layers:

1. **Data model** — normalized OHLCV candles and exchange identifiers.
2. **Signal layer** — strategies produce only directional signals.
3. **Execution model** — the backtester applies explicit stop/target and intrabar assumptions.
4. **Metrics layer** — results are converted into auditable research statistics.

This separation is intentional: public research infrastructure can evolve without exposing private production signal rules.

## Determinism

Given the same ordered candles, signal indices and configuration, the backtest result should be deterministic.

## Intrabar ambiguity

OHLCV bars do not reveal the exact sequence of high and low. When both stop and target are touched in the same bar, the default behavior is pessimistic: the stop is assumed to occur first. This can be changed explicitly with `pessimistic_intrabar=False`.

## Out of scope

- Live order execution
- Exchange credentials
- Private production signals
- Proprietary strategy thresholds
