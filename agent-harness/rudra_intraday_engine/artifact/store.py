"""Content-addressed artifact store.

Every emitted TradeSignal is written as JSON to:

    $HOME/state/<config_sha256>/signals/<hash>.json

The path is content-addressed (the filename IS the SHA-256 of the
canonical JSON). The directory is partitioned by config_sha256 so
different runs don't collide. State is append-only; nothing in the
engine ever deletes a file.

The hash is what `rudra-intraday explain <hash>` and
`rudra-intraday verify <hash>` operate on. The user copies the hash
out of the `run` output, then uses it to navigate the audit trail.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..signal_types import TradeSignal, canonical_hash


# Root of all artifact state. Resolved at write time so tests can
# override via $HOME.
def _state_root() -> Path:
    return Path(os.path.expanduser("~")) / "state"


def _signal_dir(config_sha256: str) -> Path:
    return _state_root() / config_sha256 / "signals"


def write_trade_signal(ts: TradeSignal, config_sha256: str) -> Path:
    """Write a TradeSignal to the content-addressed store.

    Returns the absolute path of the written file. The filename is
    `<canonical_hash>.json` so the same signal always lands at the
    same path (idempotent).
    """
    if not config_sha256:
        raise ValueError("config_sha256 is required for content addressing")
    d = _signal_dir(config_sha256)
    d.mkdir(parents=True, exist_ok=True)

    payload = ts.to_json_dict()
    h = canonical_hash(payload)
    out = d / f"{h}.json"

    # Atomic write: temp file + rename
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tmp-signal-", dir=str(d))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp_path, out)
    except Exception:
        # Clean up temp file on failure
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    return out


def read_trade_signal(sha256: str) -> Optional[TradeSignal]:
    """Look up a TradeSignal by its content hash.

    Scans all config directories under $HOME/state/. Returns None
    if the hash is not found.
    """
    root = _state_root()
    if not root.exists():
        return None
    matches = list(root.glob(f"*/signals/{sha256}.json"))
    if not matches:
        return None
    if len(matches) > 1:
        # Same hash under multiple configs — return the first, but
        # log the ambiguity (caller can disambiguate by config_sha).
        pass
    with matches[0].open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return _signal_from_json(payload)


def _signal_from_json(d: dict) -> TradeSignal:
    """Reconstruct a TradeSignal from a JSON dict.

    Every field of TradeSignal is reconstructed so the canonical-hash
    round-trip is exact (verify re-computes the same hash).
    """
    from ..signal_types import Action, MarketStateHash
    msh = d.get("market_state_hash")
    return TradeSignal(
        action=Action(d["action"]),
        is_decide_no=bool(d.get("is_decide_no", False)),
        decide_no_reasons=list(d.get("decide_no_reasons", [])),
        symbol=str(d.get("symbol", "")),
        qty=int(d.get("qty", 0)),
        order_type=d.get("order_type", "limit"),
        entry_price=d.get("entry_price"),
        stop_loss=d.get("stop_loss"),
        take_profit=d.get("take_profit"),
        confidence=float(d.get("confidence", 0.0)),
        reason=str(d.get("reason", "")),
        book_signal_ref=str(d.get("book_signal_ref", "")),
        kronos_signal_ref=str(d.get("kronos_signal_ref", "")),
        adjudicator_version=str(d.get("adjudicator_version", "")),
        adjudicator_commit=str(d.get("adjudicator_commit", "")),
        book_engine_version=str(d.get("book_engine_version", "")),
        predictor_version=str(d.get("predictor_version", "")),
        market_state_hash=(
            MarketStateHash(
                algorithm=msh.get("algorithm", "sha256"),
                value=msh.get("value", ""),
                data_kind=msh.get("data_kind", ""),
                data_ref=msh.get("data_ref", ""),
            ) if msh else None
        ),
        counterfactuals=list(d.get("counterfactuals", [])),
        timestamp_unix=int(d.get("timestamp_unix", 0)),
        timestamp_iso=str(d.get("timestamp_iso", "")),
    )


__all__ = [
    "write_trade_signal",
    "read_trade_signal",
]
