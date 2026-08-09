"""Packaging for the rudra-intraday-engine CLI.

Standalone Python package `rudra_intraday_engine`. Mirrors the
cli-rudra-intraday structure (sibling project).

Architecture: three layers — book engine (always), Kronos predictor
(optional, gated), Adjudicator (TOML, the user's policy layer). See
ARCHITECTURE.md at the repo root for the full design.
"""

from setuptools import find_packages, setup

setup(
    name="rudra-intraday-engine",
    version="0.1.0",
    description=(
        "Trading signal CLI for 'Mind Markets And Money' — book rules + "
        "optional Kronos ML + Adjudicator TOML. Designed for AI trading agents."
    ),
    packages=find_packages(
        where=".", include=["rudra_intraday_engine", "rudra_intraday_engine.*"]
    ),
    include_package_data=True,
    package_data={
        "rudra_intraday_engine": [
            "data/*.json",
            "data/fixtures/*.csv",
        ],
    },
    install_requires=[
        "click>=8.0.0",
        "pydantic>=2.0.0",
    ],
    # Optional Kronos predictor — installed only if user opts in
    extras_require={
        "kronos": [
            "torch>=2.0.0",
            "numpy>=1.24.0",
            "pandas>=2.0.0",
        ],
        "yfinance": [
            "yfinance>=0.2.30",
        ],
        "all": [
            "torch>=2.0.0",
            "numpy>=1.24.0",
            "pandas>=2.0.0",
            "yfinance>=0.2.30",
        ],
    },
    entry_points={
        "console_scripts": [
            "rudra-intraday=rudra_intraday_engine.cli:_main",
        ],
    },
    python_requires=">=3.10",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Topic :: Office/Business :: Financial :: Investment",
    ],
)
