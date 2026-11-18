"""설정 파일·환경 변수 로딩."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from app.fare_engine.rules import FareRules

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def load_fare_rules(path: Path | None = None) -> FareRules:
    with open(path or CONFIG_DIR / "fare_rules.yaml", encoding="utf-8") as f:
        return FareRules(**yaml.safe_load(f))


def load_app_config(path: Path | None = None) -> dict:
    with open(path or CONFIG_DIR / "app.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def fare_rules() -> FareRules:
    return load_fare_rules()


@lru_cache
def app_config() -> dict:
    return load_app_config()


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v else default
