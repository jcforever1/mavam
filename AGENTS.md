# rudra-intraday-engine — Project Rules

This file is the source of truth for how work happens in the `rudra-intraday-engine` project (and any future book-to-engine + cli-anything builds at this level). It auto-loads when a coding agent starts in this project. Read it before any non-trivial change.

## 0. Build-State Note (loaded 2026-08-08)

The Idea Roast Council ruled FIX FIRST (twice — once without Kronos, once with). The user explicitly **waived** the Book-Signature Test preflight that the council had proposed. **The foundation risk is on JC**, not the agent. The architecture is robust to ambiguous book rules because the Adjudicator is explicit policy (TOML), not compiled-from-book code. Proceed with the build; flag the waived test in the final report so JC is not surprised if book rules turn out to be ambiguous in edge cases.

## 1. "Done" Definition — STOP when this bar is met

A skill build is **done** when ALL of these are true. Not before. Not after.

- [ ] Every chapter of "Mind Markets And Money" has at least one rule encoded in `core/`
- [ ] Every encoded rule is a pure function (no I/O, no `time`, no `random`, no `requests`) — verified by a static linter
- [ ] `rudra-intraday run <book-only.toml>` produces a TradeSignal on 5 historical US fixtures with documented expected day-types
- [ ] `rudra-intraday explain <hash>` renders the signal as a human narrative
- [ ] `rudra-intraday verify <hash>` regenerates the signal and asserts equality
- [ ] 80+ tests passing (not 144 — diminishing returns past 80)
- [ ] All 4 SKILL.md copies synced and byte-identical
- [ ] README, examples, deploy recipes
- [ ] GitHub repo + push

After the bar is met, **STOP**. Do not "improve" past done. Do not add features because "what if someone...". The next book is more valuable than another hour of polish on this one.

## 2. Build Cycle (12-15 hours per book, time-boxed)

| Phase | Time | Output |
|---|---|---|
| 0. Foundation | 1 hr | `config_schema.py`, `signal_types.py` (the typed contract) |
| 1. Book engine | 8 hr | `core/profile.py`, `core/classify.py`, `core/orderflow.py`, `core/predictor.py` (Kronos, gated) |
| 2. Adjudicator | 3 hr | `adjudicator/schema.py`, `adjudicator/loader.py`, `adjudicator/merger.py` |
| 3. Artifact | 3 hr | `artifact/types.py`, `artifact/store.py`, `artifact/explain.py`, `artifact/verify.py` |
| 4. CLI | 2 hr | `cli.py` (argv-strict) |
| 5. Tests | 3 hr | Unit + fixture + CLI + E2E |
| 6. Examples | 1 hr | `strategies/*.toml`, `trading_bot_template.py` |
| 7. GitHub | 10 min | Repo + push |

If a phase is going long, **cut scope, not time**. The polish phase (artifact + examples) is the most dangerous — that's where the "what's missing?" loop lives.

## 3. Decision Filter — for every "should I add X?"

Before adding any feature post-shipping, ask in order:

1. **Does it increase TOC coverage?** → DO
2. **Does it increase natural-language routing?** → N/A (this is an engine, not a router)
3. **Does it increase durability (survives upgrades)?** → DO
4. **Does it increase observability (explain/verify work)?** → DO
5. **Is it a "what if someone..." hypothetical?** → DEFER

The last category is where most post-shipping features die. The bar for shipping a feature post-MVP is **>1 user explicitly needs it**, not "I could imagine someone wanting it."

## 4. The Three-Layer Architecture (the durable work)

### Layer 1 — The signal is the artifact
Every emitted signal is a typed, content-addressed JSON record. It carries the rule cascade trace, rejected rules with reason codes, top-N counterfactuals, model+rule versions, market-state hash, and a first-class `DECIDE_NO` variant. The artifact is the API. `rudra-intraday explain <hash>` renders it as a human narrative. `rudra-intraday verify <hash>` regenerates and asserts equality.

### Layer 2 — The adjudicator is data, not code
The book rules emit a `BookSignal`. The optional Kronos predictor emits a `KronosSignal`. **A third component — the Adjudicator, a versioned TOML file — is the only thing that can merge them into a TradeSignal.** The Adjudicator is data. Users fork it, version it in git, share it. ML never directly emits a trade to the bot.

### Layer 3 — Minimal attack surface
The CLI takes exactly one positional argv: an absolute path to a TOML config. No flags on the main verb. The process never reads `os.environ`, never traverses `$HOME` for dotfiles, never opens an inbound socket. The TOML is parsed into a strict allowlist schema. Every artifact is content-addressed under `$HOME/state/<config_sha256>/<kind>/<sha256>.<ext>`.

## 5. Naming & Layout (convention)

- **Skill name** (Mavis discovery): `rudra-intraday-engine` (hyphens)
- **CLI command** (installed): `rudra-intraday` (hyphens)
- **Python package**: `rudra_intraday_engine` (underscores)
- **Console script entry point**: `rudra-intraday = rudra_intraday_engine.cli:main`
- **Test directory**: `agent-harness/tests/`
- **Skill description frontmatter**: MUST be verb-first, MUST include "when to use" + concrete command examples, MUST be YAML-quoted (`description: >-`)

## 6. SKILL.md sync (4 copies, byte-identical)

Every shipped build must sync all 4 SKILL.md locations:

1. `agent-harness/rudra_intraday_engine/skills/SKILL.md` (canonical)
2. `skills/rudra-intraday-engine/SKILL.md` (under `rudra-intraday-engine/`)
3. `<workspace>/skills/rudra-intraday-engine/SKILL.md` (Mavis discovery)
4. `<minimax>/skills/rudra-intraday-engine/SKILL.md` (Mavis discovery, symlink-target)

Sync command pattern:
```bash
SRC=agent-harness/rudra_intraday_engine/skills/SKILL.md
for dst in skills/rudra-intraday-engine/SKILL.md \
           <workspace>/skills/rudra-intraday-engine/SKILL.md \
           <minimax>/skills/rudra-intraday-engine/SKILL.md; do
  cp "$SRC" "$dst"
done
```

Verify with `wc -c` on all 4 — must match.

## 7. Plan-First → Greenlight Gate

For any non-trivial work, present a plan first (PR-style brief: files touched, approach, tests, docs, tradeoffs). Wait for explicit greenlight ("ok" / "yes" / "go" / thumbs-up). Silence does NOT count as greenlight. Vague "hmm" / "ok" / "continue" without engaging the plan itself does NOT count.

The plan is a CHECKPOINT, not the deliverable. After approval, ship the finished product, not a "I'll start by..." next-steps list.

**The user has explicitly waived the Book-Signature Test for v1.** This is the only instance in the project history where the greenlight gate is bypassed. Recorded here so it is not forgotten.

## 8. Operating Rhythm

- **One book per week** is the target cadence. 50 books/year = a serious corpus.
- **Book selection is the bottleneck**, not execution. Maintain a ranked list of 20 candidate books. Pick from the top 5.
- **End-of-build reflection**: what worked, what was wasted, what was missing. One durable lesson → agent memory. The next build is faster because of it.

## 9. Anti-Patterns (do not do)

- ❌ Adding features after the done bar is met
- ❌ Re-running the full test suite after every change (use parametrized subsets)
- ❌ Hand-tuning every alias before shipping (use `diagnose --fix` post-ship, not here — that's a cli-rudra-intraday pattern; this engine doesn't have aliases but the principle of "let the system self-heal" applies)
- ❌ Re-verifying MinerU beyond one spot-check (trust the tool after that)
- ❌ Asking the user to clarify what you can figure out from the data
- ❌ Handing back a to-do list of commands instead of executing them
- ❌ Treating "polish past done" as the same as "ship finished" — they're opposite
- ❌ Adding flags to the main CLI verb (the single-arg surface IS the security model)
- ❌ Reading `os.environ` or `~/.bashrc` in the engine (the TOML is the only input)
- ❌ Letting ML (Kronos) emit a trade directly (the Adjudicator is the only merger)

## 10. Tradeoffs (be honest)

1. **The Adjudicator TOML is a DSL.** Every DSL becomes a programming language. The design bar: every legitimate 5-min intraday strategy expressible in TOML.
2. **Content-addressed state grows forever.** Append-only. A separate prune tool (not invoked by the agent) handles GC. Acceptable for v1; might need `rudra-intraday compact` in v2.
3. **The signal-as-artifact schema is a load-bearing contract.** Every consumer (bot, explain, verify, replay) consumes the same JSON. Pin with content-hash and version.
4. **No live tick data in v1.** Kronos is a partial substitute (denoise + forecast). Real order flow still needs L2/L3 feeds. Architecture supports adding a tick source later without changing the artifact schema.
5. **One positional argv is opinionated.** Flag-creep is the #1 way CLIs become unauditable. The user explicitly cited audit as a non-negotiable.
6. **The Book-Signature Test was waived.** Foundation risk is on JC. The architecture is robust to ambiguous book rules because the Adjudicator is explicit policy, not compiled-from-book code.
