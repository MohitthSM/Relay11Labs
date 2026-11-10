import os

# Keep a real key for `-m live` tests, then force mock mode and fast time BEFORE relay is
# imported anywhere.
os.environ["RELAY_LIVE_ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "")
os.environ.update(
    RELAY_LLM_MODE="mock", RELAY_TTS_MODE="mock", RELAY_CHAT_MODE="mock", RELAY_CRM_MODE="mock",
    RELAY_TIME_SCALE="1000", ANTHROPIC_API_KEY="", ELEVENLABS_API_KEY="", SLACK_BOT_TOKEN="",
    SLACK_SIGNING_SECRET="", SALESFORCE_ACCESS_TOKEN="", RELAY_MOCK_HALLUCINATE="false",
)  # fmt: skip

import pytest  # noqa: E402
from sqlmodel import Session  # noqa: E402

from relay.config import Settings  # noqa: E402
from relay.db import init_db, make_engine  # noqa: E402
from relay.providers import Connectors  # noqa: E402
from relay.providers.chat_mock import MockChatProvider  # noqa: E402
from relay.providers.crm_mock import MockSalesforceProvider  # noqa: E402
from relay.providers.llm_mock import MockLLMProvider  # noqa: E402
from relay.providers.tts_mock import MockTTSProvider  # noqa: E402
from relay.workflow.context import Context  # noqa: E402
from relay.workflow.definitions import WORKFLOWS  # noqa: E402


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def conns():
    return Connectors(
        llm=MockLLMProvider(), chat=MockChatProvider(), crm=MockSalesforceProvider(),
        tts=MockTTSProvider(),
    )  # fmt: skip


@pytest.fixture
def make_ctx(conns):
    """Build a Context sharing the same connectors: lets a test simulate a restart."""

    def build() -> Context:
        settings = Settings(connector_max_attempts=3, connector_base_delay_s=0.5)
        return Context(conns=conns, settings=settings, workflows=WORKFLOWS)

    return build


@pytest.fixture
def ctx(make_ctx):
    return make_ctx()


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
def settings():
    return Settings()
