"""Settings and mock/live resolution. Keys come from .env only."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["auto", "mock", "live"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    elevenlabs_api_key: str = ""
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    salesforce_access_token: str = ""

    relay_llm_mode: Mode = "auto"
    relay_tts_mode: Mode = "auto"
    relay_chat_mode: Mode = "auto"
    relay_crm_mode: Mode = "auto"

    script_model_default: str = "claude-haiku-5-5"
    script_model_escalation: str = "claude-sonnet-5-5"
    relay_time_scale: float = 1.0  # read ONLY by timeutil.py
    relay_db_url: str = "sqlite:///data/relay.db"

    connector_max_attempts: int = 4
    connector_base_delay_s: float = 0.5
    prompts_dir: str = "prompts"


def resolve_mode(mode: Mode, key: str) -> str:
    """'live' only if a key is present and the mode is not forced to mock."""
    if mode == "mock" or not key:
        return "mock"
    return "live"


@lru_cache
def get_settings() -> Settings:
    return Settings()
