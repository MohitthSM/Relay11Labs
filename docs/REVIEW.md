# Phase 8 code review

A senior Python review of the whole codebase (backend `relay/`, `scripts/`, `tests/`, and the frontend where a backend change reached it). Baseline before the review: 1460 tests, ruff, pyright and `npm run typecheck` all green. Each finding is listed with its fix; "kept" means it was looked at and deliberately left as it is.

## Fixed

| # | Area | Finding | Fix |
|---|---|---|---|
| 1 | Correctness (spec) | **R11 was not implemented.** Passenger names stayed in `Event.payload`, `Event.event_key`, `Announcement.script` / `spoken_text` and the audio cache forever; `RELAY_PII_RETENTION_HOURS` was in `.env.example` but not in `Settings`. | `relay/purge.py`: an event key whose versions have all ended more than the retention ago is scrubbed (name → `[purged]`, key → `purged-<id>`, staff note dropped, scripts → `[purged]`, its non-permanent clips deleted unless a running announcement shares them). Runs every 5 real minutes from the lifespan. `tests/test_purge.py`. |
| 2 | Correctness | **Live → Demo left TfL events playing** over a scripted scenario (the tube policy has no expiry). | `events.withdraw` + `tfl.set_mode`: switching to demo withdraws every active TfL event (jobs cancelled, no good-service message). A lock makes the switch and a poll in flight mutually exclusive. `tests/test_source_switch.py`. |
| 3 | Correctness | **Demo → Live kept a running scenario posting scripted events**, so "severe delays" from a script could play next to real TfL data. | A scenario step now runs only in demo mode, under the same lock; a waiting run notices a switch within 0.5 s and ends as `stopped`; the switch withdraws events the scenario made. Tests in `test_source_switch.py`. |
| 4 | Correctness | `events._persist_plan` linked an announcement to its `LLMCall` **by model name**. The API can report a different model string than the one requested (server-side refusal fallback), and default and escalation can be configured to the same model: either way `llm_call_id` was wrong or empty, so the audit log lost the prompt version. | `ScriptSlot.attempt`; announcements are matched to the call of the attempt that wrote them. Test in `test_engine.py`. |
| 5 | Error handling | `voice.render_many` only caught `ProviderError`. An unmapped SDK exception, a disk error, or a failed stitch (no `ffmpeg` for MP3) escaped and turned the whole event into a 500, against "never 500 for a provider failure". | Any exception for one segment or one stitch becomes that announcement's `audio_error`; the other clips still render. Two tests in `test_voice.py`. |
| 6 | Dead code | `voice.warm_fixed_segments` was tested but never called, so the first live announcement always paid for the fixed opening and closing. `db.session_dependency` was unused. | The lifespan warms fixed segments once at start-up when TTS is live (a no-op once cached). `session_dependency` removed. |
| 7 | Typing | Six `# type: ignore[arg-type]` where a payload dict was passed to `EventInput(...)`. | `EventInput.of(...)` validates the dict through `model_validate`; no ignores left in `relay/` for it. Found a real `Optional` on the staff-note path in the process (`draft.payload` checked now). |
| 8 | Observability | `llm_cost` / `tts_cost` returned `$0` for a model missing from the price table, silently, so a typo in `.env` made the metrics page lie. | A warning is logged once per unpriced model. Price sources and date in the module docstring. |
| 9 | Docs drift | `api/ws.py` listed the message types without `event.withdrawn` and `source`. | Docstring updated. |
| 10 | Test gaps | No tests for the source switch in either direction, purge, the attempt link, or non-provider failures in the voice layer. | 17 new tests (1460 → 1477). |

## Kept, with reasons

- **Broad `except Exception` in the four background loops** (scheduler, TfL poller, replay runner, purge, plus per-line in the poller). Each one logs with `log.exception` and is there so one bad tick cannot stop the app; narrowing them would trade a logged error for a dead task.
- **`asyncio.to_thread` for sync DB work from async code.** Matches CLAUDE.md (sync SQLModel, sync tick). The scheduler's `State.lock` and the feed's new lock are `threading.Lock`s because the work runs in worker threads, not on the loop.
- **`bus.publish` from worker threads** uses `call_soon_threadsafe` per subscriber loop: correct. A disconnected WebSocket is noticed on its next send; that can hold a queue of at most 100 messages until then. Acceptable for a single-process demo.
- **The `ws.py` send loop catches only `WebSocketDisconnect`.** Other errors propagate after the `finally` unsubscribes; catching more would hide bugs.
- **`assert` for invariants after `flush()`** (ids not None). These are type narrowing for pyright on states the ORM guarantees, not input validation; inputs are validated by Pydantic and answered with 4xx.
- **The fact lock, templates and normaliser** were read for I/O and clock use: none (pure, cached lexicons), as CLAUDE.md requires.
- **`subscriber_count` in `bus.py`** is used only by a test. It is one line and documents the bus's state; kept.

## Not verified here (no API keys in this environment)

See the "Live verification" section of PLAN.md Phase 8: the `-m live` smoke tests, the pronunciation sync, the real-audio samples and the model comparison could not run because no ElevenLabs or Anthropic key was available to this session.
