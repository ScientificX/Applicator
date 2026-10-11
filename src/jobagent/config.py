"""Typed configuration: env settings + the YAML files under config/.

Single source of truth is plan-general.md (section 2.1 criteria, 2.2 visa, 10 model).
The YAML files are generated from it; keep them in step.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


class Settings(BaseSettings):
    """Environment (see .env.example). Values are read case-insensitively."""

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_model_escalate: str = "deepseek-v4-pro"

    # Mail worker (inbound only)
    agent_token: str = ""
    mail_worker_base: str = "https://my-email-worker.flso.workers.dev"

    # Addresses
    jobagent_contact_address: str = ""

    # Local models
    ollama_base: str = "http://localhost:11434"
    ollama_classify_model: str = "qwen3:8b"
    ollama_failover_model: str = "qwen3:14b"
    ollama_embed_model: str = "bge-m3"

    # Budget (hard caps)
    jobagent_max_cost_per_run_usd: float = 1.00
    jobagent_max_cost_per_day_usd: float = 3.00

    # Paths (repo-relative unless absolute)
    jobagent_state_dir: Path = Path("state")
    jobagent_artifacts_dir: Path = Path("artifacts")

    def _resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else ROOT / p

    @property
    def state_dir(self) -> Path:
        return self._resolve(self.jobagent_state_dir)

    @property
    def artifacts_dir(self) -> Path:
        return self._resolve(self.jobagent_artifacts_dir)

    @property
    def db_path(self) -> Path:
        return self.state_dir / "jobagent.db"

    def ensure_dirs(self) -> None:
        for d in (self.state_dir, self.artifacts_dir, self.artifacts_dir / "har"):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# YAML models (validated so a typo fails loudly at startup, not mid-run)
# ---------------------------------------------------------------------------


class Locations(BaseModel):
    preferred: list[str] = Field(default_factory=list)
    next: list[str] = Field(default_factory=list)
    also: list[str] = Field(default_factory=list)


class Comp(BaseModel):
    IE_min_eur: int | None = None
    UK_min_gbp: int | None = None
    remote_min_eur: int | None = None


class Target(BaseModel):
    seniority: list[str]
    title_include: list[str]
    title_exclude: list[str]
    stack_must: list[str] = Field(default_factory=list)
    stack_nice: list[str] = Field(default_factory=list)
    domain_nice: list[str] = Field(default_factory=list)
    locations: Locations = Field(default_factory=Locations)
    comp: Comp = Field(default_factory=Comp)
    top_k_per_run: int = 25
    fresh_window_minutes: int = 60


class Criteria(BaseModel):
    target: Target


class VisaRules(BaseModel):
    right_to_work: dict[str, str]
    jd_filter: dict[str, list[str]] = Field(default_factory=dict)
    answers: dict[str, dict[str, str]] = Field(default_factory=dict)


class Visa(BaseModel):
    """Mirrors visa.yaml, whose top-level key is `visa` (same pattern as the others)."""

    visa: VisaRules


class Tenants(BaseModel):
    tenants: list[dict[str, Any]] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)


class Answers(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    disclosures: dict[str, str] = Field(default_factory=dict)


class Proxies(BaseModel):
    pools: dict[str, list[str]] = Field(default_factory=dict)


def read_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"missing config file: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_criteria() -> Criteria:
    return Criteria.model_validate(read_yaml("criteria.yaml"))


def load_visa() -> Visa:
    return Visa.model_validate(read_yaml("visa.yaml"))


def load_tenants() -> Tenants:
    return Tenants.model_validate(read_yaml("tenants.yaml"))


def load_answers() -> Answers:
    return Answers.model_validate(read_yaml("answers.yaml"))


def load_proxies() -> Proxies:
    return Proxies.model_validate(read_yaml("proxies.yaml"))


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
