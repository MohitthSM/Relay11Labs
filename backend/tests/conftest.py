import os

# Keep a real key for `-m live` tests, then force mock mode and fast time BEFORE relay is
# imported anywhere.
os.environ["RELAY_LIVE_ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "")
os.environ["RELAY_LIVE_ELEVENLABS_API_KEY"] = os.environ.get("ELEVENLABS_API_KEY", "")
os.environ.update(
    RELAY_LLM_MODE="mock", RELAY_TTS_MODE="mock", RELAY_TIME_SCALE="1000",
    ANTHROPIC_API_KEY="", ELEVENLABS_API_KEY="", RELAY_MOCK_HALLUCINATE="false",
)  # fmt: skip

import pytest  # noqa: E402
from sqlmodel import Session  # noqa: E402

from relay.config import Settings  # noqa: E402
from relay.db import init_db, make_engine  # noqa: E402


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture(autouse=True)
def reset_time_scale():
    from relay import timeutil

    timeutil.set_scale(None)
    yield
    timeutil.set_scale(None)


@pytest.fixture
def engine(db_url):
    eng = make_engine(db_url)
    init_db(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def settings(tmp_path, db_url):
    """Mock-mode settings with a temp database and audio directory."""
    return Settings(relay_db_url=db_url, audio_dir=str(tmp_path / "audio"))


@pytest.fixture
def app(settings):
    from relay.main import create_app

    return create_app(settings)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:  # the context manager runs the lifespan (creates tables)
        yield c
