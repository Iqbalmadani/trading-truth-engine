# Trading Truth Engine (TTE)

Trading Truth Engine is an experimental Python project for analyzing cryptocurrency market data and producing research signals. It is a prototype, not a production-ready trading system.

> **Risk notice:** TTE does not place exchange orders. Its signals, stop-loss/take-profit levels, leverage estimates, backtests, and historical reports are not verified performance claims or financial advice. Do not use them to trade real capital without independent review and testing.

## What it does

- Retrieves public market data through the configured exchange connectors.
- Validates and analyzes market data, including change-point and regime analysis.
- Produces candidate LONG, SHORT, or neutral signals and records results locally.
- Uses a local SQLite database; no database or trading history is included in this repository.

The implementation has defaults and fixed configuration values. It should not be treated as a fully self-calibrating system. Historical documents in `docs/` describe work and experiments at the time they were written; they are not independent audits or proof of current behavior.

## What it does not do

- It does not submit, manage, or cancel exchange orders. Despite the historical `run_live.py` filename, the script generates signals; it does not execute trades.
- The current connectors use public market-data endpoints and do not require API keys. API-key environment variables described in older documentation are not used by this release.
- It does not guarantee signal accuracy, profitability, or suitability for any trading strategy.

## Requirements and installation

Python 3.12 or newer is required.
This release was verified with Python 3.14.2; other Python versions were not tested here.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.

## Verify the installation

The test suite is intended to run offline:

```powershell
python -m pytest -q
```

Inspect the command options before running a pipeline:

```powershell
python scripts/run_live.py --help
python scripts/run_calibration.py --help
```

Running a pipeline (rather than requesting `--help`) may retrieve public market data and write to the local database. Review its configuration and source code before running it.

## Configuration and local data

Project configuration is under `config/`. Review the exchange, pair, scope, and threshold settings before running any scripts.

The application creates `data/tte.db` when its database module initializes. This repository excludes `data/`, logs, local environments, caches, and SQLite files. A fresh clone therefore has no prior local trading or backtest history.

## Project layout

- `src/layer1/`: data connectors and validation
- `src/layer2/`: market structure and regime analysis
- `src/layer3/`: signal and risk-related calculations
- `src/layer4/`: portfolio/circuit-breaker and signal-history support; not an exchange execution engine
- `scripts/`: research, diagnostic, calibration, and signal-generation entry points
- `config/`: project configuration
- `tests/`: automated tests
- `docs/`: project notes; consult the documentation index for context and limitations

## Documentation

Start with [`docs/README.md`](docs/README.md). Many retained notes are historical and may not match the current code. The implementation and tests are the source of truth; historical audit or backtest documents should not be interpreted as certification or evidence of expected returns.

## License

Released under the MIT License. See [`LICENSE`](LICENSE).
