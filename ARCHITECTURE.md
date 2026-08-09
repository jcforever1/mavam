# mavam — Architecture

> **Renamed from `rudra-intraday-engine` to `mavam` on 2026-08-09 to
> avoid name collision with the sibling project `cli-rudra-intraday`
> (the teaching CLI). The book is "MIND MARKETS AND MONEY" — `mavam`
> is the natural acronym.**

# rudra-intraday-engine — Architecture

> The "ship finished" plan for v1. This is the design JC greenlit on 2026-08-08, after the Idea Roast Council rendered FIX FIRST (twice) and the user explicitly waived the Book-Signature Test preflight.

## What this is

A Python CLI that re-implements the "Mind Markets And Money" book rules (Market Profile, day types, open types, order flow) as code, with an optional Kronos ML forecasting layer, and a stateless TradingView chart adapter (pinchtab / Playwright). Designed to be used by an AI trading agent.

The sibling project `cli-rudra-intraday` (the teaching CLI, 144/144 tests) is what teaches the book's content. **This project is what executes the book's rules on real data.**

## The three-layer architecture (the convergence)

Three independent ideas collapsed into one architecture:

### Layer 1 — The signal is the artifact (Branch A from /adhd)
Every emitted signal is a typed, content-addressed JSON record. It carries:
- Which rules fired and which were rejected (with reason codes)
- The top-N counterfactual alternatives + the smallest input delta that would have flipped the decision
- Model+rule versions, market-state hash
- A first-class `DECIDE_NO` variant (refusals are first-class)

The artifact is the API. `rudra-intraday explain <hash>` renders the artifact as a human narrative. `rudra-intraday verify <hash>` regenerates the signal from recorded state and asserts equality. The artifact carries its own provenance; the audit trail is the artifact, not a separate log.

### Layer 2 — The adjudicator is data, not code (Branch B)
The book rules emit a `BookSignal`. The optional Kronos predictor emits a `KronosSignal`. **A third component — the Adjudicator, a versioned TOML file — is the only thing that can merge them into a TradeSignal.** The Adjudicator is data. Users fork it, version it in git, share it. ML never directly emits a trade to the bot.

This answers the Skeptic's "the book is intuition, the ML is unverified, who has the final word?" — neither does. A versioned TOML does.

### Layer 3 — Minimal attack surface (Branch C)
The CLI takes exactly one positional argv: an absolute path to a TOML config. No flags on the main verb. The process never reads `os.environ`, never traverses `$HOME` for dotfiles, never opens an inbound socket. The TOML is parsed into a strict allowlist schema. Every artifact is content-addressed under `$HOME/state/<config_sha256>/<kind>/<sha256>.<ext>`, written via temp-then-atomic-rename.

The AI agent integrates by writing the TOML to a path only it owns, invoking the CLI as `rudra-intraday run <config.toml>`, and reading the JSON output. No env, no flags, no implicit state.

## The CLI surface (the agent's view)

```bash
# The main verb
rudra-intraday run <config.toml>
#   -> emits TradeSignal JSON to stdout
#   -> writes content-addressed to $HOME/state/<config_sha256>/signals/<hash>.json

# On-call workflow
rudra-intraday explain <hash>     # human narrative of any past signal
rudra-intraday verify <hash>      # re-execute from recorded state, assert equality
rudra-intraday replay <hash>      # re-run with a different Adjudicator TOML on the same signals
rudra-intraday audit <hash>       # show full provenance chain

# Conditional: only if Kronos test passes
rudra-intraday predict <data.csv>  # one-shot Kronos inference on a CSV
```

That's it. One verb on the main path, four on the audit path, one optional.

## The package layout

```
rudra-intraday-engine/                       ← new repo (sibling to cli-rudra-intraday)
├── ARCHITECTURE.md                          ← this file
├── AGENTS.md                                ← project rules (the "done" definition)
├── README.md                                ← GitHub landing page
├── agent-harness/
│   ├── setup.py                             ← entry point: rudra-intraday
│   ├── MANIFEST.in
│   ├── README.md                            ← package install doc
│   ├── rudra_intraday_engine/               ← the package
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── cli.py                           # argv-strict entry
│   │   ├── config_schema.py                 # Branch C: strict TOML allowlist
│   │   ├── signal_types.py                  # Branch A: typed signal dataclasses
│   │   ├── core/
│   │   │   ├── profile.py                   # TPO, POC, VA, IB
│   │   │   ├── classify.py                  # day_type, open_type, balance, initiative, trend
│   │   │   ├── orderflow.py                 # tick-volume delta proxy
│   │   │   └── predictor.py                 # Kronos adapter (optional, gated)
│   │   ├── adjudicator/
│   │   │   ├── schema.py                    # Pydantic schema for adjudicator TOML
│   │   │   ├── loader.py                    # load + validate
│   │   │   └── merger.py                    # ~200 LoC pure function: (TOML, signals) -> TradeSignal
│   │   ├── artifact/
│   │   │   ├── types.py                     # SignalArtifact, RuleTrace, Counterfactual, MarketStateHash
│   │   │   ├── store.py                     # content-addressed write to $HOME/state/
│   │   │   ├── explain.py                   # `rudra-intraday explain <hash>`
│   │   │   └── verify.py                    # `rudra-intraday verify <hash>`
│   │   ├── data/
│   │   │   ├── loader.py                    # CSV + yfinance US tickers
│   │   │   └── fixtures/                    # historical US fixtures
│   │   └── tests/
│   ├── examples/
│   │   ├── strategies/
│   │   │   ├── book-only.toml               # v1 default, no Kronos
│   │   │   └── conservative-5min.toml       # book + kronos must agree (v1.1)
│   │   └── trading_bot_template.py          # reads TradeSignal, executes via broker
│   ├── scripts/
│   └── tests/
│       ├── test_config_schema.py
│       ├── test_signal_types.py
│       ├── test_artifact.py
│       ├── test_adjudicator.py
│       └── test_cli.py
├── skills/
│   └── rudra-intraday-engine/
│       └── SKILL.md                          ← Mavis skill discovery
└── deploy/
    ├── Dockerfile
    └── rudra-intraday-engine.service         # systemd unit
```

## How the layers compose (data flow)

```
                   ┌─────────────────────────────────────┐
                   │  $HOME/state/<config_sha256>/     │
                   │  config.toml                       │  ← single input surface (Branch C)
                   │  (predictor: book-only | kronos)   │
                   └──────────────┬──────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────┴─────────────────────────┐
        │                                                    │
        ▼                                                    ▼
  ┌──────────────┐                                   ┌──────────────┐
  │   profile/   │  -> TPO, POC, Value Area,         │  predictor/   │
  │   classify/  │     Initial Balance               │  (Kronos)     │  <- optional,
  │   orderflow/ │                                   │               │     gated by
  │              │  BookSignal:                      │  KronosSignal: │     config.toml
  │  book engine │  - rules fired                    │  - prediction  │
  │  (always)    │  - rules rejected (reason codes)  │  - confidence  │
  │              │  - rule_version                   │  - model_ver   │
  └──────┬───────┘                                   └──────┬───────┘
         │                                                  │
         └────────────────────┬─────────────────────────────┘
                              │
                              ▼
                  ┌──────────────────────┐
                  │     adjudicator/     │  <- data (TOML), not code
                  │                      │     user can fork, version, share
                  │  (TOML, signals)     │
                  │       ->             │
                  │   TradeSignal        │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │     artifact/        │  <- content-addressed JSON
                  │                      │     stored in $HOME/state/
                  │  TradeSignal         │
                  │  (typed, hash)       │
                  └──────────────────────┘
                             │
                             ▼
                          STDOUT
                       (JSON to bot)
```

## Adjudicator TOML (the user's policy layer)

```toml
[adjudicator]
name = "conservative-5min"
version = "1.2.0"
description = "Conservative 5-min: book and Kronos must agree"

[inputs.book]
required = true
min_rules_fired = 2
required_rules = ["primary_trend", "volatility_ok"]
blocked_rules = []

[inputs.kronos]
required = true
min_confidence = 0.65
allowed_model_versions = ["kronos-0.6.0", "kronos-0.6.1"]

[fallback]
when_no_kronos = "HOLD"
when_no_book = "HOLD"
when_validation_fails = "HOLD"

[[merge.rules]]
when = "book.action == 'BUY' and kronos.prediction == 'UP' and kronos.confidence >= 0.70"
emit = "BUY"
size_multiplier = 1.0
reason = "book-buy-kronos-confirms"

[[merge.rules]]
when = "book.action == 'SELL' and kronos.prediction == 'DOWN' and kronos.confidence >= 0.70"
emit = "SELL"
size_multiplier = 1.0
reason = "book-sell-kronos-confirms"

[[merge.rules]]
when = "book.action != 'HOLD' and kronos.prediction != book.action"
emit = "HOLD"
reason = "book-kronos-disagreement"

[risk]
max_position_pct = 0.05
stop_loss_pct = 0.015
take_profit_pct = 0.045
max_daily_trades = 6
```

Same architecture, two TOMLs: `book-only.toml` (v1 default) and `conservative-5min.toml` (v1.1 if Kronos test passes). The book engine doesn't know which is in use; the Adjudicator does. The bot doesn't know either.

## The "done" definition (from council ruling)

A skill build is **done** when ALL of these are true:

- [ ] Every chapter of "Mind Markets And Money" has at least one rule encoded in `core/`
- [ ] Every encoded rule is a pure function (no I/O, no `time`, no `random`, no `requests`) — verified by a static linter
- [ ] `rudra-intraday run <book-only.toml>` produces a TradeSignal on 5 historical US fixtures with documented expected day-types
- [ ] `rudra-intraday explain <hash>` renders the signal as a human narrative
- [ ] `rudra-intraday verify <hash>` regenerates the signal and asserts equality
- [ ] 80+ tests passing (not 144 — diminishing returns past 80)
- [ ] All 4 SKILL.md copies synced
- [ ] README, examples, deploy recipes
- [ ] GitHub repo + push

After the bar is met, **STOP**. Do not "improve" past done. Do not add features because "what if someone...". The next book is more valuable than another hour of polish on this one.

## The risk register (from the council)

| Risk | Mitigation |
|---|---|
| Book is intuition, not spec | The Adjudicator is data. Users can audit the merge, not just the code. |
| Kronos unverified for 5-min | Predictor is optional + gated by config. Same Adjudicator works with or without. |
| pinchtab SPOF | TradingView integration is pull-based, stateless, with explicit `stale_after_unix`. The CLI never pushes. Shipped as `data/pinchtab.py` + `[data.chart]` TOML section. **pinchtab is a CDP client to the user's *local* TradingView Desktop** running with `--remote-debugging-port=9222` — not a scraper of TradingView's servers. The user must start their desktop with that flag; the engine then connects via `playwright.connect_over_cdp("http://localhost:9222")`. yfinance is the alternative data source for when no desktop is running. |

## Data sources (3 options, in priority order)

| Source | Substrate | When to use | ToS |
|---|---|---|---|
| **yfinance** | HTTP, no browser | Default. Real Yahoo Finance data, well-maintained, no browser needed. `pip install rudra-intraday-engine[yfinance]` | Yahoo API terms |
| **pinchtab** | CDP to local TradingView Desktop | When you specifically want TradingView's chart data from your own local desktop app. `pip install rudra-intraday-engine[pinchtab]` | TradingView ToS restricts automated use of extracted data; user accepts |
| **crawl4ai** (v1.1) | HTTP scraping via [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai) | When the user wants to scrape non-API sources (MarketWatch, Investing.com, custom sites). Future addition. | Per-site ToS |
| Auditability collapse | TradeSignal carries provenance chain. `rudra-intraday verify <hash>` regenerates and asserts. |
| 22-30 hr build | The Adjudicator + Artifact + Content-Addressed State is the durable work. The book engine is the differentiating work. |
| JC waived the Book-Signature Test | Foundation risk is on JC. The architecture is robust to ambiguous book rules because the Adjudicator is explicit policy, not compiled-from-book code. |

## Build sequence (the steps)

1. **Scaffold** — package structure, `setup.py`, `__init__.py`, `__main__.py` (1 hr)
2. **config_schema.py** — Branch C foundation, strict TOML allowlist (1 hr)
3. **signal_types.py** — Branch A foundation, typed signal dataclasses (1 hr)
4. **core/profile.py** — TPO, POC, Value Area, Initial Balance (3 hr)
5. **core/classify.py** — Day type, open type, balance, initiative, trend (4 hr)
6. **core/orderflow.py** — Tick-volume delta proxy (1 hr)
7. **core/predictor.py** — Kronos adapter (gated by config) (1 hr)
8. **adjudicator/schema.py + loader.py + merger.py** — TOML + merge logic (3 hr)
9. **artifact/types.py + store.py + explain.py + verify.py** — Content-addressed artifact (3 hr)
10. **cli.py** — Argv-strict entry, 5 commands (2 hr)
11. **tests** — Unit + fixture + CLI + E2E (3 hr)
12. **examples** — strategies/*.toml + trading_bot_template.py (1 hr)
13. **GitHub repo + push** (10 min)

**Total: ~22-25 hours.**
