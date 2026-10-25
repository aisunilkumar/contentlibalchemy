"""Settings: env vars (.env) + config/<APP_ENV>.yaml."""
import os
from dataclasses import dataclass, fields
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    env: str = "development"
    quality_threshold: float = 7.5
    max_revisions: int = 2
    min_blog_words: int = 600
    det_weight: float = 0.4
    history_window: int = 10
    max_sources: int = 12
    max_failed_checks: int = 1      # more failing rule checks than this forces a revision, whatever the judge says
    search_fanout: int = 2          # how many search providers to query per sub-query
    linkedin_fold_chars: int = 210  # approx. text visible before LinkedIn's 'see more' (heuristic)
    services_path: str = str(ROOT / "config" / "services.yaml")
    brand_voice_path: str = str(ROOT / "config" / "brand_voice.yaml")


def load_settings() -> Settings:
    env = os.getenv("APP_ENV", "development")
    s = Settings(env=env)
    p = ROOT / "config" / f"{env}.yaml"
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
        names = {f.name for f in fields(Settings)}
        for k, v in data.items():
            if k in names:
                setattr(s, k, v)
    return s


settings = load_settings()
