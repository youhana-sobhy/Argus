"""Runtime configuration, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _str(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    return default if val is None or val == "" else val


@dataclass(frozen=True)
class Settings:
    secret_key: str
    runtime_dir: Path
    database_url: str
    connector: str
    apic_url: str
    apic_username: str
    apic_password: str = field(repr=False)
    apic_verify_tls: bool
    allow_live_execution: bool
    llm_provider: str
    anthropic_model: str
    azure_endpoint: str
    azure_api_key: str = field(repr=False)
    azure_deployment: str
    azure_api_version: str
    redact: bool
    demo_password: str = field(repr=False)

    @property
    def is_mock(self) -> bool:
        return self.connector == "mock"


@lru_cache
def get_settings() -> Settings:
    _load_dotenv()
    runtime = Path(_str("AUTOHERO_RUNTIME_DIR", "./runtime")).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    return Settings(
        secret_key=_str("AUTOHERO_SECRET_KEY", "dev-only-insecure-key"),
        runtime_dir=runtime,
        database_url=_str("AUTOHERO_DATABASE_URL", f"sqlite:///{(runtime / 'autohero.db').as_posix()}"),
        connector=_str("AUTOHERO_CONNECTOR", "mock").lower(),
        apic_url=_str("AUTOHERO_APIC_URL", ""),
        apic_username=_str("AUTOHERO_APIC_USERNAME", ""),
        apic_password=_str("AUTOHERO_APIC_PASSWORD", ""),
        apic_verify_tls=_bool("AUTOHERO_APIC_VERIFY_TLS", True),
        allow_live_execution=_bool("AUTOHERO_ALLOW_LIVE_EXECUTION", False),
        llm_provider=_str("AUTOHERO_LLM_PROVIDER", "mock").lower(),
        anthropic_model=_str("AUTOHERO_ANTHROPIC_MODEL", "claude-opus-5-5"),
        azure_endpoint=_str("AZURE_OPENAI_ENDPOINT", ""),
        azure_api_key=_str("AZURE_OPENAI_API_KEY", ""),
        azure_deployment=_str("AZURE_OPENAI_DEPLOYMENT", ""),
        azure_api_version=_str("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        redact=_bool("AUTOHERO_REDACT", True),
        demo_password=_str("AUTOHERO_DEMO_PASSWORD", "AutoHero-Demo-2026"),
    )
