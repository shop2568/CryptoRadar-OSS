# CryptoRadar OSS

[![CI](https://github.com/shop2568/CryptoRadar-OSS/actions/workflows/ci.yml/badge.svg)](https://github.com/shop2568/CryptoRadar-OSS/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

CryptoRadar OSS is an open-source research toolkit for reproducible cryptocurrency strategy testing. It provides exchange-neutral candle models, deterministic R-multiple backtesting, performance metrics, CSV ingestion, and forward-validation building blocks.

The public project is intentionally separated from the maintainer's private production trading system. It does **not** include private API keys, live-account credentials, proprietary production signals, or undisclosed trading rules.

## Why this project exists

Quant research often becomes hard to reproduce because exchange formats, strategy assumptions, and performance calculations are mixed together. CryptoRadar OSS keeps these layers separate so researchers can test ideas with explicit assumptions and comparable outputs.

## Current features

- Exchange-neutral OHLCV candle model
- Binance, Bitget, BingX and generic exchange identifiers
- CSV candle loader with validation and deterministic ordering
- Simple long/short R-multiple backtest engine
- Configurable stop distance and reward-to-risk target
- Win rate, profit factor, net R and maximum drawdown metrics
- Reproducible example strategy
- Unit tests and GitHub Actions CI
- No exchange credentials required for the core research workflow

## Installation

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e .
```

Development:

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## Quick start

```python
from datetime import datetime, timezone

from cryptoradar import Candle, Side, BacktestConfig, run_backtest

candles = [
    Candle(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc), 100, 102, 99, 101, 1000),
    Candle(datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc), 101, 106, 100, 105, 1400),
]

result = run_backtest(
    candles=candles,
    signals={0: Side.LONG},
    config=BacktestConfig(stop_pct=0.01, reward_r=4.0),
)

print(result.metrics)
```

See [`examples/quickstart.py`](examples/quickstart.py).

## Research principles

1. **Reproducibility** — the same candles and parameters should produce the same result.
2. **Explicit assumptions** — stop, target and intrabar conflict handling are configured instead of hidden.
3. **Separation of concerns** — data normalization, strategy signals, execution assumptions and metrics are independent.
4. **Forward validation** — research should be evaluated on unseen data, not only optimized historical windows.
5. **Security by design** — secrets and production-only trading logic do not belong in this repository.

## Project layout

```text
src/cryptoradar/      Core library
examples/             Runnable examples
tests/                Regression and unit tests
docs/                 Architecture notes
.github/workflows/     Continuous integration
```

## Roadmap

- Public-market-data adapters
- Richer position sizing
- Walk-forward helpers
- Standardized forward out-of-sample collectors
- Additional performance diagnostics

Production signal logic remains intentionally out of scope.

## Contributing

Issues and pull requests are welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md).

For security issues, follow [`SECURITY.md`](SECURITY.md) instead of opening a public issue.

## Disclaimer

This project is for research and educational purposes only. It is not financial advice and does not guarantee trading performance.

## License

MIT License. See [`LICENSE`](LICENSE).
