---
name: rudra-intraday-engine
description: >-
  RUN a 5-min intraday trading strategy that re-implements the "Mind
  Markets And Money" book rules (Market Profile, day types, open
  types, order flow) as Python. Use when the user wants to: classify
  a trading day (trend-up / trend-down / P-day / B-day / normal /
  neutral / double-distribution); compute Market Profile primitives
  (TPO, POC, Value Area 70%, Initial Balance); run a book-only
  strategy; or evaluate an Adjudicator TOML that merges book + Kronos
  signals. The CLI is argv-strict (one positional arg, no flags) and
  emits a typed TradeSignal JSON. Invoke with `rudra-intraday run
  <config.toml>`; the output JSON has action, symbol, qty,
  entry/stop/target prices, confidence, and full provenance. Use
  `rudra-intraday explain <hash>` to render any past signal as text.
  NOT for backtesting frameworks, NOT for live broker integration
  (use a bot template), NOT for sub-minute bars (book is 30-min slot
  based).
---

# rudra-intraday-engine

A trading signal CLI that re-implements the rules from "Mind Markets
And Money" by CA Rudra Murthy BV. Pure-Python, no ML required, designed
to be used by an AI trading agent.

## What it does

- Computes Market Profile primitives from intraday OHLCV bars: TPOs,
  Point of Control, Value Area (70% rule), Initial Balance.
- Classifies each session into one of 6 day types (trend-up,
  trend-down, P-day, B-day, normal-day, neutral, double-distribution).
- Classifies the open into one of 6 open types (open-drive,
  open-test-drive, open-rejection-reverse, open-range-extension,
  open-range-transition, open-auction).
- Computes a tick-volume delta proxy + cumulative delta + divergence.
- (Optional) Wraps Kronos as a gated ML predictor (off by default).
- Merges book + Kronos via a user-authored **Adjudicator TOML**.
- Emits a typed `TradeSignal` JSON to stdout + content-addressed
  storage under `$HOME/state/<config_sha256>/signals/<hash>.json`.

## Quick start

```bash
# Install (editable, for development)
pip install -e .

# Or install the released package
pip install rudra-intraday-engine

# Run an example strategy
rudra-intraday run examples/configs/book-only.toml
```

The example config uses `examples/strategies/book-only.toml` (the
v1 default — book rules only, no Kronos) and a synthetic SPY
trend-up CSV at `examples/data/spy-trend-up-2026-08-10.csv`.

## Commands

```bash
rudra-intraday run <config.toml>      # main verb; emits TradeSignal JSON
rudra-intraday explain <hash>         # render a past signal as text
rudra-intraday verify <hash>          # re-canonicalize, assert equality
rudra-intraday predict <data.csv>     # one-shot Kronos (requires [kronos] extra)
```

## Output (the API)

The `run` command emits a JSON object on stdout:

```json
{
  "action": "BUY",
  "is_decide_no": false,
  "decide_no_reasons": [],
  "symbol": "SPY-TREND-UP-2026-08-10",
  "qty": 5,
  "order_type": "limit",
  "entry_price": 105.85,
  "stop_loss": 104.26225,
  "take_profit": 110.61325,
  "confidence": 0.75,
  "reason": "book-buy-high-confidence",
  "book_signal_ref": "def365f626184379858e95566a696db6ba5050b08bef349729b32ccf47e5e80b",
  "kronos_signal_ref": "",
  "adjudicator_version": "1.0.0",
  "book_engine_version": "0.1.0",
  "predictor_version": "",
  "_artifact_path": "/Users/.../state/.../signals/<hash>.json"
}
```

The bot reads `action`, `symbol`, `qty`, `entry_price`, `stop_loss`,
`take_profit` and ignores everything else. The provenance fields
(`book_signal_ref`, etc.) are for the audit trail.

## The Adjudicator TOML (the user's policy layer)

The user authors an Adjudicator TOML with their merge rules:

```toml
[adjudicator]
name = "my-strategy"
version = "1.0.0"

[adjudicator.book]
required = true
min_rules_fired = 3

[[adjudicator.merge_rules]]
when = "book.action == 'BUY' and book.confidence >= 0.70"
emit = "BUY"
size_multiplier = 1.0
reason = "book-buy-high-confidence"

[adjudicator.risk]
max_position_pct = 0.05
stop_loss_pct = 0.015
take_profit_pct = 0.045
```

The `when` expressions are safe AST-walked (no `eval`, no function
calls, no index access). The merger is a pure function of
(Adjudicator, BookSignal, KronosSignal).

## Bot integration (subprocess pattern)

```python
import subprocess, json

# 1. Write the config
config_path = "/path/to/my-config.toml"

# 2. Run the engine
result = subprocess.run(
    ["rudra-intraday", "run", config_path],
    capture_output=True, text=True, check=True,
)
signal = json.loads(result.stdout)

# 3. Act on the signal
if not signal["is_decide_no"]:
    broker.submit(signal)
```

See `examples/trading_bot_template.py` for a complete starter.

## Architecture (the three layers)

1. **Signal-as-artifact** — every signal is a typed, content-addressed
   JSON record with full provenance (rule versions, model versions,
   market-state hash, counterfactuals, DECIDE_NO as first-class).
2. **Adjudicator is data, not code** — the book rules emit a
   `BookSignal`, Kronos emits a `KronosSignal`, a versioned TOML is
   the only thing that merges them into a `TradeSignal`. Users fork
   the TOML in git, not the code.
3. **Minimal attack surface** — the CLI takes exactly one positional
   argv (the config path). No flags, no env vars, no `$HOME` walks,
   no inbound sockets. Every artifact is content-addressed and
   written via temp-then-atomic-rename.

## Requirements

- Python 3.10+ (uses tomllib from stdlib on 3.11+)
- For Kronos: `pip install rudra-intraday-engine[kronos]`
  (torch, numpy, pandas + the Kronos package)

## Limitations (v1)

- The book is intuition, not spec. The Adjudicator-as-TOML is the
  durability mechanism for ambiguous book rules — the user tunes the
  policy, not the code.
- Order flow uses a candle-structure proxy (no L2/L3 data).
- CSV input only (no yfinance ticker mode in v1).
- 30-min slot granularity (per the book's convention).
- No live tick data; no broker integration in the engine itself.
