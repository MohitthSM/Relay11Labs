from dataclasses import dataclass

from relay.config import Settings
from relay.providers import Connectors
from relay.workflow.steps import Workflow


@dataclass
class Context:
    """Everything a step may touch: connectors, settings, known workflows."""

    conns: Connectors
    settings: Settings
    workflows: dict[str, Workflow]
