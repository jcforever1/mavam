"""The book engine — Market Profile primitives from Mind Markets And Money.

Re-implements the foundational computations:
- profile.py: TPO, POC, Value Area (70%), Initial Balance
- classify.py: day type, open type, balance, initiative, trend
- orderflow.py: tick-volume delta proxy, cumulative delta
- predictor.py: Kronos ML adapter (optional, gated by config)

The book engine is always the base layer. The optional Kronos
predictor is an additive layer; the Adjudicator merges them.
"""

__version__ = "0.1.0"
