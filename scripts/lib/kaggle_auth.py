#!/usr/bin/env python3
"""Kaggle credential presence checks (access_token or legacy kaggle.json).

OS/arch assumptions: macOS (darwin) or Linux, Python 3.14+ (repo venv).
"""

from __future__ import annotations

from pathlib import Path

KAGGLE_ACCESS_TOKEN = Path.home() / ".kaggle" / "access_token"
KAGGLE_JSON = Path.home() / ".kaggle" / "kaggle.json"


def kaggle_credentials_present() -> bool:
    return KAGGLE_ACCESS_TOKEN.is_file() or KAGGLE_JSON.is_file()


def kaggle_credentials_error() -> str:
    return (
        "KAGGLE_CREDENTIALS_MISSING configure either "
        f"{KAGGLE_ACCESS_TOKEN} (bearer token) or "
        f"{KAGGLE_JSON} (username and key)"
    )
