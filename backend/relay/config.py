"""Settings and mock/live resolution. Keys come from .env only."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["auto", "mock", "live"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    elevenlabs_api_key: str = ""

    relay_llm_mode: Mode = "auto"
    relay_tts_mode: Mode = "auto"

    script_model_default: str = "claude-haiku-5-5"
    script_model_escalation: str = "claude-sonnet-5-5"
    anthropic_server_fallback: bool = False
    llm_timeout_s: float = 6.0
    relay_mock_hallucinate: bool = False  # mock LLM injects one false fact per event (demo)
    relay_time_scale: float = 1.0  # read ONLY by timeutil.py
    relay_db_url: str = "sqlite:///data/relay.db"
    relay_pii_retention_hours: float = 24.0  # R11: real hours after a page ends (purge.py)

    # Real data (Phase 7). Demo mode plays the scripted scenarios; live mode polls TfL.
    relay_data_mode: Literal["demo", "live"] = "demo"  # the start-up mode; the UI can switch it
    relay_tfl_lines: str = "jubilee"  # comma-separated TfL line ids, e.g. "jubilee,victoria"
    relay_tfl_poll_s: float = 60.0  # real seconds, never time-scaled
    relay_tfl_site: str = "tube_station"
    tfl_app_key: str = ""  # optional: TfL answers without a key at a low rate limit

    # Voice layer. Model routing table (voice.py ROUTES): urgent/live clips use the lowest-latency
    # model, scheduled and escalation clips use the highest-quality one.
    elevenlabs_model_live: str = "eleven_v4_turbo"
    elevenlabs_model_scheduled: str = "eleven_v4"
    elevenlabs_output_format: str = "mp3_44100_128"
    elevenlabs_max_concurrency: int = 2
    elevenlabs_timeout_s: float = 20.0
    audio_dir: str = "data/audio"
    # Cut fixed openings/closings off scripts and voice them once (D18). Off: every announcement
    # is voiced whole, so it is one clip per announcement and nothing is stitched but the chime.
    voice_split_segments: bool = True

    prompts_dir: str = "prompts"


def resolve_mode(mode: Mode, key: str) -> str:
    """'live' only if a key is present and the mode is not forced to mock."""
    if mode == "mock" or not key:
        return "mock"
    return "live"


@lru_cache
def get_settings() -> Settings:
    return Settings()
