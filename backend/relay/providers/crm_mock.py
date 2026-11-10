"""Mock Salesforce: in-memory records, one per idempotency key."""

from typing import Any

from relay.providers.crm_base import CRMRecord
from relay.providers.errors import FailureQueue


class MockSalesforceProvider:
    mode = "mock"

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self._by_key: dict[str, CRMRecord] = {}
        self.failures = FailureQueue()

    def create_record(
        self, object_type: str, fields: dict[str, Any], *, idempotency_key: str
    ) -> CRMRecord:
        self.failures.maybe_raise()
        if idempotency_key in self._by_key:
            first = self._by_key[idempotency_key]
            return CRMRecord(first.id, first.object_type, created=False)
        record = CRMRecord(f"500{len(self.records):012d}", object_type, created=True)
        self.records[record.id] = {"type": object_type, **fields}
        self._by_key[idempotency_key] = record
        return record
