# rudra-intraday-engine

> A trading signal CLI for the book *"Mind Markets And Money"* (CA Rudra Murthy B V & Indrazith Shantharaj, Notion Press 2019). Re-implements the book's Market Profile, day-type, open-type, and order-flow rules as Python, with an optional Kronos ML forecasting layer and a thin TradingView integration. Designed to be used by an AI trading agent.

The sibling project [`cli-rudra-intraday`](https://github.com/jcforever1/cli-rudra-intraday) is what *teaches* the book's content. **This project is what *executes* the book's rules on real data.**

## Architecture (TL;DR)

Three layers, each answering a different failure mode:

1. **The signal is the artifact** — every emitted signal is a typed, content-addressed JSON record. It carries the rule cascade trace, rejected rules with reason codes, top-N counterfactuals, model+rule versions, and a first-class `DECIDE_NO` variant. The artifact is the API.

2. **The adjudicator is data, not code** — the book rules emit a `BookSignal`, the optional Kronos predictor emits a `KronosSignal`, and a versioned TOML file (the Adjudicator) is the **only** thing that can merge them into a `TradeSignal`. The Adjudicator is data — users fork it, version it in git, share it. ML never directly emits a trade to the bot.

3. **Minimal attack surface** — the CLI takes exactly one positional argv (a TOML config path). No flags on the main verb. The process never reads `os.environ`, never traverses `$HOME` for dotfiles, never opens an inbound socket.

See `ARCHITECTURE.md` for the full design.

## Quick start

```bash
# Install (from this repo)
git clone https://github.com/jcforever1/rudra-intraday-engine
cd rudra-intraday-engine
python3 -m pip install -e agent-harness/

# Verify
rudra-intraday --version
# -> rudra-intraday, version 0.1.0

# Run on a config
rudra-intraday run examples/strategies/book-only.toml
# -> emits a TradeSignal as JSON, writes a content-addressed artifact
#    to $HOME/state/<config_sha256>/signals/<hash>.json

# On-call workflow
rudra-intraday explain <hash>     # human narrative of any past signal
rudra-intraday verify <hash>      # regenerate and assert equality
```

## CLI surface (the agent's view)

```bash
rudra-intraday run <config.toml>      # the main verb — one TOML arg, JSON out
rudra-intraday explain <hash>         # human narrative of any past signal
rudra-intraday verify <hash>          # regenerate and assert equality
rudra-intraday replay <hash>          # re-run with a different Adjudicator TOML
rudra-intraday audit <hash>           # show full provenance chain
rudra-intraday predict <data.csv>     # optional: one-shot Kronos inference
```

That's it. No flags on the main verb. The TOML is the only input surface.

## Strategies (Adjudicator TOMLs)

Same architecture, two modes:

- **`book-only.toml`** (v1 default) — book rules only, no Kronos. Always works.
- **`conservative-5min.toml`** (v1.1) — book + Kronos must agree. Requires the optional Kronos predictor to be enabled in the user's `config.toml`.

Users can write their own adjudicator TOMLs and share them. The engine doesn't care which adjudicator is in use; the adjudicator is data.

## What's in v1

- Book rules encoded from "Mind Markets And Money" (Market Profile, day types, open types, order flow)
- Optional Kronos ML predictor (gated by config)
- Adjudicator TOML — the policy layer, data not code
- Content-addressed signal artifacts
- `explain` / `verify` / `replay` / `audit` for on-call
- 80+ tests, 5 historical US fixtures, reference trading bot

## What's NOT in v1

- Live tick data (real order flow needs L2/L3 feeds; Kronos is a partial substitute)
- Broker integration (the engine emits signals; the bot executes)
- Real-time streaming (v1 is per-request; the artifact + replay model supports streaming in v2)
- `compact` for state-dir GC (state grows; v2 will add this)

## How a bot uses this

```python
import subprocess, json

# 1. Write a config
config = """
[adjudicator]
file = "/path/to/conservative-5min.toml"

[data]
ticker = "SPY"
date = "2024-01-15"
lookback_days = 5
"""

with open("/tmp/bot-config.toml", "w") as f:
    f.write(config)

# 2. Run the engine
result = subprocess.run(
    ["rudra-intraday", "run", "/tmp/bot-config.toml"],
    capture_output=True, text=True, check=True
)
signal = json.loads(result.stdout)

# 3. Execute the signal (your broker integration)
if signal["action"] in ("BUY", "SELL"):
    broker.place_order(
        side=signal["action"],
        symbol="SPY",
        qty=signal["qty"],
        order_type="limit",
        limit_price=signal["entry_price"],
    )

# 4. Audit (optional, but good practice)
artifact_hash = signal["artifact_hash"]
subprocess.run(["rudra-intraday", "verify", artifact_hash], check=True)
```

The bot is intentionally dumb. It reads `TradeSignal`, calls the broker, writes the signal to audit. Zero knowledge of book or ML.

## Tradeoffs

1. **Book is intuition, not spec.** The Adjudicator (TOML) is the auditable surface, not the code. The user explicitly waived the Book-Signature Test that would have verified the book rules encode unambiguously before building.
2. **Kronos unverified for 5-min intraday.** The original Kronos paper claims daily K-lines, not 5-min. The predictor is optional — `book-only.toml` works without it.
3. **Content-addressed state grows forever.** A separate prune tool handles GC; not in v1.
4. **One positional argv is opinionated.** Flag-creep is the #1 way CLIs become unauditable. This is by design.
5. **The architecture is the durable work.** The book engine is the differentiating work. Both ship together.

## Attribution

- **Book**: *"Mind Markets And Money — a successful journey into intraday"* by CA Rudra Murthy B V & Indrazith Shantharaj, Notion Press 2019.
- **Kronos**: foundation model by shiyu-coder, AAAI 2026, MIT. 36K stars on GitHub.
- **Sibling project**: `cli-rudra-intraday` — the teaching CLI, JC's own work.
- **Architecture**: derived from the Idea Roast Council (Believer, Skeptic, Investor, Judge) held 2026-08-08.

## License

The vendored book content (rules encoded in `core/`) belongs to the authors. The code (everything else) is MIT — use it, fork it, ship your own engine with it.
