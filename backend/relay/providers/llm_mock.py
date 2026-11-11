"""Mock LLM: works with no key and no network.

- generate_scripts: the reference template for each slot. With `hallucinate=True`
  (RELAY_MOCK_HALLUCINATE) it adds one invented fact to the last slot, on every attempt, so the
  fact lock rejects it, the retry is rejected too, and the template is used (the stage demo).
- extract_event: the deterministic staff-note rules (script/note_rules.py).

Tests can queue exact responses in `responses` (JSON text, or an Exception to raise); they are
consumed in order by whichever method is called next.
"""

import json
from typing import Any

from relay.providers.errors import FailureQueue
from relay.providers.llm_base import ExtractRequest, LLMResult, NoteExtraction, ScriptRequest
from relay.script import templates
from relay.script.note_rules import rule_extract

# One invented fact per event type: a claim no event of that type supports.
HALLUCINATIONS = {
    "passenger_page": {
        "en": "Your bags will be offloaded.",
        "es": "Su equipaje será retirado del avión.",
        "fr": "Vos bagages seront débarqués.",
    },
    "line_status": {
        "en": "Trains are also being diverted.",
        "es": "Los trenes también están siendo desviados.",
        "fr": "Les trains sont également déviés.",
    },
    "stand_change": {
        "en": "This service is cancelled.",
        "es": "Este servicio está cancelado.",
        "fr": "Ce service est annulé.",
    },
}


class MockLLMProvider:
    mode = "mock"

    def __init__(
        self, responses: list[str | Exception] | None = None, *, hallucinate: bool = False
    ) -> None:
        self.responses = list(responses or [])
        self.hallucinate = hallucinate
        self.calls: list[dict[str, Any]] = []
        self.failures = FailureQueue()

    def _next(self, kind: str, prompt: str, model: str, default: str) -> LLMResult:
        self.calls.append({"kind": kind, "prompt": prompt, "model": model})
        self.failures.maybe_raise()
        item = self.responses.pop(0) if self.responses else default
        if isinstance(item, Exception):
            raise item
        return LLMResult(
            text=item,
            model=model,
            input_tokens=len(prompt) // 4,
            output_tokens=len(item) // 4,
            latency_ms=1,
        )

    def generate_scripts(self, req: ScriptRequest, model: str) -> LLMResult:
        scripts = []
        for lang, urgency in req.slots:
            text = templates.draft(req.event_type, req.facts, urgency, lang, update=req.update)
            scripts.append({"language": lang, "urgency": urgency, "text": text})
        if self.hallucinate and scripts:
            last = scripts[-1]
            last["text"] += " " + HALLUCINATIONS[req.event_type][last["language"]]
        return self._next("scripts", req.system + req.user, model, json.dumps({"scripts": scripts}))

    def extract_event(self, req: ExtractRequest, model: str) -> LLMResult:
        found = NoteExtraction(**rule_extract(req.note, req.site))
        return self._next("extract", req.system + req.user, model, found.model_dump_json())
