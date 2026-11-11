"""Engine, session dependency, create_all."""

from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from relay.config import get_settings


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().relay_db_url
    return create_engine(url, connect_args={"check_same_thread": False})


def init_db(engine: Engine) -> None:
    import relay.models  # noqa: F401  (registers tables)
    import relay.workflow.models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def session_dependency(engine: Engine):
    """Build a FastAPI dependency bound to one engine."""

    def get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    return get_session
