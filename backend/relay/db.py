"""Engine, session dependency, create_all."""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from relay.config import get_settings


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().relay_db_url
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args={"check_same_thread": False})


def init_db(engine: Engine) -> None:
    import relay.models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(engine)


def session_dependency(engine: Engine):
    """Build a FastAPI dependency bound to one engine."""

    def get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    return get_session
