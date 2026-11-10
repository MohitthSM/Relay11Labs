from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class CRMRecord:
    id: str
    object_type: str
    created: bool  # False if the idempotency key matched an existing record


class CRMProvider(Protocol):
    mode: str

    def create_record(
        self, object_type: str, fields: dict[str, Any], *, idempotency_key: str
    ) -> CRMRecord: ...
