"""Application configuration management with environment variable loading and validation."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False


PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_PROVIDERS = {
    "openai",
    "openai_compatible",
    "groq",
    "gemini_openai",
    "ollama",
    "deterministic",
}

PLACEHOLDER_KEYS = {
    "",
    "your-api-key-here",
    "your_api_key_here",
    "replace-with-your-key",
    "changeme",
    "none",
    "null",
}


class ConfigurationError(RuntimeError):
    """Raised when required application or LLM configuration settings are missing or invalid."""


@dataclass(frozen=True)
class AppConfig:
    """Immutable configuration object for the Employee Information Multi-Agent System."""

    llm_provider: str = "openai_compatible"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_temperature: float = 0.0
    database_path: Path = PROJECT_ROOT / "data" / "employee_system.db"
    log_level: str = "INFO"
    log_file_path: Path = PROJECT_ROOT / "logs" / "app.log"
    enable_Fallback_parser: bool = False

    def validate_for_live_llm(self) -> None:
        """Validate that provider, model, and API key are properly configured for live LLM calls."""
        provider = (self.llm_provider or "").strip().lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise ConfigurationError(
                f"Unsupported LLM_PROVIDER '{self.llm_provider}'. "
                f"Supported providers: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
            )
        if not (self.llm_model or "").strip():
            raise ConfigurationError(
                "Missing required configuration: LLM_MODEL must be set to a non-empty model name."
            )
        if provider not in {"deterministic", "ollama"}:
            key_clean = (self.llm_api_key or "").strip()
            if key_clean.lower() in PLACEHOLDER_KEYS:
                raise ConfigurationError(
                    "Missing or placeholder LLM_API_KEY. Set a valid API key in your environment or .env file "
                    "(or set LLM_PROVIDER=deterministic for offline/test execution)."
                )


def load_config(env_file: Optional[Path] = None, override_db_path: Optional[Path] = None) -> AppConfig:
    """Load configuration from environment variables (and optional .env file)."""
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=True)
    else:
        default_env = PROJECT_ROOT / ".env"
        if default_env.exists():
            load_dotenv(dotenv_path=default_env, override=True)

    provider = os.getenv("LLM_PROVIDER", "openai_compatible").strip().lower()
    model = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL")
    if base_url is not None:
        base_url = base_url.strip() or None

    # Provider default base URLs for free-tier OpenAI-compatible services
    if base_url is None:
        if provider == "groq":
            base_url = "https://api.groq.com/openai/v1"
        elif provider == "gemini_openai":
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif provider == "ollama":
            base_url = "http://localhost:11434/v1"

    temp_str = os.getenv("LLM_TEMPERATURE", "0.0").strip()
    try:
        temperature = float(temp_str)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid LLM_TEMPERATURE value '{temp_str}': must be a float.") from exc

    db_path_raw = os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "employee_system.db")).strip()
    db_path = Path(override_db_path) if override_db_path is not None else Path(db_path_raw)
    if not db_path.is_absolute():
        db_path = (PROJECT_ROOT / db_path).resolve()

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigurationError(f"Invalid LOG_LEVEL '{log_level}'.")

    log_file_raw = os.getenv("LOG_FILE_PATH", str(PROJECT_ROOT / "logs" / "app.log")).strip()
    log_file_path = Path(log_file_raw)
    if not log_file_path.is_absolute():
        log_file_path = (PROJECT_ROOT / log_file_path).resolve()

    return AppConfig(
        llm_provider=provider,
        llm_model=model,
        llm_api_key=api_key,
        llm_base_url=base_url,
        llm_temperature=temperature,
        database_path=db_path,
        log_level=log_level,
        log_file_path=log_file_path,
    )
