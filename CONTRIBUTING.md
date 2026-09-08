# Contributing

Thanks for contributing to CryptoRadar OSS.

## Development setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

## Pull requests

Please keep pull requests focused and include:

- A clear description of the problem being solved
- Tests for behavior changes
- Documentation updates when public behavior changes
- No secrets, credentials, private keys, or proprietary production trading logic

## Research changes

For changes affecting backtest behavior, document the assumptions clearly. In particular, explain:

- Entry timing
- Stop and target calculation
- Intrabar conflict handling
- Fees/slippage assumptions, if applicable
- Whether the change affects reproducibility

## Security

Do not report security vulnerabilities in public issues. See `SECURITY.md`.
