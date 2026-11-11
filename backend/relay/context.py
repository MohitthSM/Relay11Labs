"""What the services and routers share: providers, settings and the database engine."""

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from relay.config import Settings
from relay.providers import Connectors


@dataclass
class Context:
    conns: Connectors
    settings: Settings
    engine: Engine
