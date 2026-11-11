# CLAUDE.md: Relay

Relay is a context-aware PA system for transport hubs, built as a pitch demo for ElevenLabs. Operational events become fact-locked, on-brand, multilingual announcements that are voiced by ElevenLabs and repeated in zones by a scheduler.

**Read before working:**
- `SPEC.md` (what and why)
- `ARCHITECTURE.md` (how, and the design decisions D1–D18)
- `PLAN.md` (which phase we are in)

Do not contradict a decision in ARCHITECTURE.md silently. If a change needs one, update the doc in the same change and say so.

## Hard constraints (never break these)

1. **Fact lock.**
   - A generated script may contain only facts present in the event payload: times, platforms, gates, stands, names, flight numbers, routes, lines, stations, causes, statuses.
   - The LLM writes `{placeholders}`, and code fills them.
   - Any violation means the script is rejected, retried once on the escalation model, and then replaced by the deterministic template for that language.
   - Never weaken a fact-lock check to make a test or demo pass. Add a lexicon or gazetteer entry, or a template, instead.
2. **Mock mode everywhere.**
   - Every LLM and TTS call goes through a provider Protocol (`relay/providers/`) that has a mock implementation which works with no API keys and no network.
   - A missing key means mock mode is used automatically. Code outside `providers/` must never import `anthropic` or `elevenlabs`.
3. **Explainable code.**
   - The owner must be able to explain every file in a Python code review.
   - Prefer plain functions and small modules over frameworks, metaclasses, decorators-with-magic, or clever generics.
   - A new dependency needs a one-line justification in the PR or commit.
4. **Secrets.**
   - Keys live only in `.env`, which is git-ignored. Commit `.env.example` with empty values.
   - Never log keys, and never put keys in the frontend.
5. **No safety-critical announcements.** Emergency, evacuation and security events are out of scope. Do not add event types for them.
6. **PII.** Passenger names must not appear in logs or the metrics API. Use the event id instead.

## Stack

- **Backend:**
  - Python 3.12 (pinned in `backend/.python-version`), FastAPI, Pydantic v2, `pydantic-settings`
  - SQLModel + SQLite
  - `anthropic` (Claude API) and `elevenlabs` (official SDK, pinned `>=2.77,<3`)
  - `mutagen` (audio duration), `pydub` (stitches clips; needs the `ffmpeg` binary for MP3; `audioop-lts` is its Python 3.13 shim), `httpx` (TfL), `pyyaml`
- **Tooling:** `uv` for env and deps, `pytest`, `pytest-asyncio`, `ruff` (lint + format), `pyright` (types, on `relay/` and `scripts/`).
- **Frontend:** React + Vite + TypeScript + Tailwind. No state library: `fetch` + `useState`/`useEffect`, and native `WebSocket` for the one real-time channel (`/ws/events`, D8).
- **Models (via `.env`):**
  - `SCRIPT_MODEL_DEFAULT=claude-haiku-5-5`
  - `SCRIPT_MODEL_ESCALATION=claude-sonnet-5-5`, used for fact-lock retries and all staff notes
  - `ELEVENLABS_MODEL_LIVE=eleven_v4_turbo` (urgent/live clips), `ELEVENLABS_MODEL_SCHEDULED=eleven_v4` (escalation levels). Routing table in `voice.py` (D17).

## Folder layout

```
relay/
├── CLAUDE.md  SPEC.md  ARCHITECTURE.md  PLAN.md  README.md
├── Makefile                       # thin wrappers for the commands below
├── .env.example                   # committed; .env is not
├── backend/
│   ├── pyproject.toml             # deps + ruff + pytest config
│   ├── relay/
│   │   ├── main.py                # FastAPI app, lifespan starts scheduler + sources
│   │   ├── config.py              # Settings (pydantic-settings), mock/live resolution
│   │   ├── db.py                  # engine, session dependency, create_all
│   │   ├── models.py              # SQLModel tables: Event, LLMCall, Announcement, AudioClip, VoiceRender, PlaybackJob, PlayLog (audit log)
│   │   ├── context.py             # Context: providers + settings + engine, shared by routers and services
│   │   ├── retry.py               # call_with_retry (TTS retries in voice.py, D14)
│   │   ├── schemas.py             # Pydantic event payloads (discriminated union) + API DTOs
│   │   ├── sites.py               # load/validate sites/*.yaml
│   │   ├── events.py              # create / update / resolve / approve, dedup; orchestrates script → voice → jobs
│   │   ├── background.py          # one worker thread: later urgency levels are voiced after first play (D4)
│   │   ├── jobs.py                # playback jobs per event version: approval rule, hold, release, cancel
│   │   ├── voice.py               # model routing, content-hash cache, segments + stitching, metrics
│   │   ├── pronunciation.py       # PLS parsing, pronunciation.lock.json
│   │   ├── pronsync.py            # create/update a site's dictionary (used by the script)
│   │   ├── scheduler.py           # 1 s tick loop: zone queues, priority preemption + resume, escalation, expiry
│   │   ├── purge.py               # R11: scrub names, scripts and audio of ended pages after the retention
│   │   ├── bus.py                 # in-memory fan-out to WebSocket clients (state changes and play commands)
│   │   ├── timeutil.py            # now(), scaled(seconds) — the ONLY place RELAY_TIME_SCALE is read
│   │   ├── prompts.py             # load versioned prompt files (backend/prompts/)
│   │   ├── script/
│   │   │   ├── engine.py          # LLM → fact lock → escalate → template; returns ScriptPlan
│   │   │   ├── factlock.py        # pure checks, no I/O beyond cached lexicons
│   │   │   ├── templates.py       # deterministic wording per type/urgency/language
│   │   │   ├── prompt.py          # loads system prompt per hub type, builds the user message
│   │   │   ├── calls.py           # one LLM attempt → LLMCallRecord (tokens, cost, outcome)
│   │   │   ├── notes.py           # staff note → draft event (screen, extract, extraction lock)
│   │   │   ├── note_rules.py      # deterministic note parser (mock LLM + fallback)
│   │   │   ├── normalize.py       # script → spoken_text
│   │   │   ├── lexicons.py        # loads lexicon/*.yaml
│   │   │   └── lexicon/           # claims/numbers/style.<lang>.yaml, notes.en.yaml
│   │   ├── providers/
│   │   │   ├── llm_base.py        # LLMProvider Protocol, LLMResult
│   │   │   ├── llm_anthropic.py
│   │   │   ├── llm_mock.py
│   │   │   ├── tts_base.py        # TTSProvider Protocol, RenderRequest, TTSResult
│   │   │   ├── tts_elevenlabs.py  # also pronunciation dictionary create/update
│   │   │   ├── tts_mock.py
│   │   │   └── pricing.py         # $ per MTok / per 1K chars
│   │   ├── sources/
│   │   │   ├── replay.py          # plays scenarios/*.json on a (scaled) timeline
│   │   │   ├── tfl.py             # Phase 7: TfL line status poller (live mode only, D23)
│   │   │   └── flights.py         # FlightFeed Protocol, mock feed, stub for a real provider
│   │   └── api/
│   │       ├── events.py  ws.py  playback.py  scenarios.py  metrics.py  sites.py  source.py
│   ├── prompts/                   # <name>.v<N>.md: script_<hub>, script_rules, note_extract
│   ├── sites/                     # tube_station.yaml, airport.yaml, bus_station.yaml, *.pls, pronunciation.lock.json
│   ├── scenarios/                 # a_jubilee.json, b_ba117.json, c_route25.json
│   ├── static/chime.wav           # generated by scripts/make_chime.py
│   ├── scripts/sync_pronunciations.py  make_chime.py  pick_voice.py  render_samples.py  simulate_day.py
│   ├── tests/                     # mirrors relay/; test_factlock.py is the biggest file
│   └── data/                      # git-ignored: relay.db, audio/
└── frontend/
    ├── package.json  vite.config.ts  tailwind.config.js
    └── src/
        ├── main.tsx  App.tsx          # header, toolbar, the one WebSocket, browser playback
        ├── api.ts  types.ts           # typed fetch helpers; the socket with reconnect
        ├── live.ts  data.ts           # useLive (shared socket), useEvents, useZones, useNow
        ├── zoneAudio.ts  status.ts  format.ts   # one <audio> per zone; event standing; display helpers
        ├── pages/Console.tsx  Zones.tsx  Metrics.tsx
        └── components/EventCard.tsx  ZoneMap.tsx  Compose.tsx  EventForm.tsx  StaffNote.tsx
                       ScriptPreview.tsx  ui.tsx
```

## Commands

Run from the repo root.

```bash
make setup        # cd backend && uv sync ; cd frontend && npm install
make dev          # backend :8000 (uvicorn --reload) + frontend :5173 (vite, proxies /api)
make up           # docker compose up --build: backend :8000, console :8080 (nginx proxies /api)
make test         # cd backend && uv run pytest -q
make lint         # ruff check + format --check + pyright (backend), then npm run typecheck (frontend)
make fmt          # uv run ruff format . && uv run ruff check --fix .
make sync-pronunciations  # upload/update sites/*.pls to ElevenLabs (needs ELEVENLABS_API_KEY)
make demo-a       # POST /scenarios/a_jubilee/run (same for demo-b, demo-c)
```

Underlying commands, if not using make:
- Backend: `cd backend && uv run uvicorn relay.main:app --reload --port 8000`
- Frontend: `cd frontend && npm run dev`
- Single test: `cd backend && uv run pytest tests/test_factlock.py -q -k gate`

## Environment (`.env.example`)

```
ANTHROPIC_API_KEY=
ELEVENLABS_API_KEY=
RELAY_LLM_MODE=auto             # auto | mock | live   (auto = live if key present)
RELAY_TTS_MODE=auto
SCRIPT_MODEL_DEFAULT=claude-haiku-5-5
SCRIPT_MODEL_ESCALATION=claude-sonnet-5-5
ANTHROPIC_SERVER_FALLBACK=false
LLM_TIMEOUT_S=6
ELEVENLABS_MODEL_LIVE=eleven_v4_turbo      # urgent / live announcements: lowest latency
ELEVENLABS_MODEL_SCHEDULED=eleven_v4       # scheduled / escalation: highest quality
ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
ELEVENLABS_MAX_CONCURRENCY=2
ELEVENLABS_TIMEOUT_S=20
AUDIO_DIR=data/audio
VOICE_SPLIT_SEGMENTS=true        # false: voice each announcement whole (no fixed opening/closing)
RELAY_WARM_SEGMENTS=false        # true: voice fixed phrases at start-up (live TTS; ~260 chars once)
RELAY_TIME_SCALE=1              # 60 = one minute passes in one second
RELAY_MOCK_HALLUCINATE=false    # mock LLM injects one false fact per event (demo)
RELAY_PII_RETENTION_HOURS=24
RELAY_DB_URL=sqlite:///data/relay.db
TFL_APP_KEY=                    # optional; TfL works without it at low rate
RELAY_DATA_MODE=demo            # demo | live: start-up source; the console toggle changes it
RELAY_TFL_LINES=jubilee         # comma-separated TfL line ids, polled in live mode
RELAY_TFL_POLL_S=60             # real seconds, never time-scaled
RELAY_TFL_SITE=tube_station
```

Voice ids and pronunciation dictionary locators are set per site in `sites/*.yaml`, not in `.env`.

## Conventions

**Python**
- Type hints everywhere. `from __future__ import annotations` is not needed on 3.12.
- Ruff config: line length 100, rules `E,F,I,UP,B,SIM`.
- Functions over classes. Classes only for Pydantic/SQLModel models and provider implementations.
- Pure core, thin edges:
  - `factlock.py`, `templates.py`, `normalize.py` and scheduler decision logic do no I/O and take explicit arguments (lexicon and prompt files are read once and cached).
  - Time comes in as a parameter (`now: datetime`), never from `datetime.now()` inside logic.
- Sync SQLModel sessions inside FastAPI's threadpool are fine. Later urgency levels are voiced on `background.py`'s one worker thread, which opens its own session; tests call `background.wait()` before they look at those clips. Only `scheduler.run`, `bus.py`, the replay runner and the WebSocket endpoint are `async`; the scheduler tick (`scheduler.step`) is sync and takes `now`, so tests drive it with a fake clock. Blocking provider calls are run with `asyncio.to_thread`.
- All intervals go through `timeutil.scaled()`. Never `asyncio.sleep(<literal>)` outside `timeutil`.
- Each event payload field that is a fact is declared in `schemas.py` with `Field(json_schema_extra={"fact": True})`. The fact lock and the prompt read that metadata, so the fact list has a single source.
- Errors: providers raise `ProviderError`, and the engine catches it and falls back. API handlers return 4xx for validation errors and never 500 for a provider failure.
- Logging: `logging` stdlib, one logger per module, no PII. Metrics come from DB rows, not from log parsing.

**LLM prompts**
- System prompts live in versioned files `backend/prompts/<name>.v<N>.md` (`string.Template`, `$var`): one per hub type (`script_underground`, `script_airport`, `script_bus`) with house style and few-shot examples, the shared `script_rules`, and `note_extract`. Never edit a released version in place: add `v<N+1>`. `script/prompt.py` loads the highest version and builds the user message in code. The version ref (`script_airport.v1+script_rules.v1`) is stored on every LLM call record.
- The user message names value facts only as `{placeholders}`, never their values (so passenger names are not sent to the LLM). Staff notes go in `<staff_note>` tags as data and never reach the script prompt.
- The output is always structured (Pydantic model through `messages.parse`). Never regex-parse free text.
- Do not send `temperature`/`top_p`/`top_k`, assistant prefill, or forced `tool_choice`. Current models reject them.

**ElevenLabs**
- Only `providers/tts_elevenlabs.py` imports `elevenlabs`.
- Always pass `model_id`, `language_code`, `output_format` and `seed` explicitly.
- Every request carries the site's pronunciation dictionary version, fixed segments included. One voice per site, in every language.
- Never store or log the spoken text (names): `VoiceRender` and `AudioClip` hold ids, counts and timings only.
- Never put SSML or `<break>` in text, because v4 ignores or doesn't support it.
- Audio tags and IPA are inserted only by code (`normalize.py`), never by the LLM. The fact lock rejects `[ ] < > /` in LLM text.

**Tests**
- Every fact-lock layer has positive and negative cases in EN, ES and FR. Every bug that slips past the lock becomes a test.
- Tests always run in mock mode. `conftest.py` forces `RELAY_LLM_MODE=mock`, `RELAY_TTS_MODE=mock`, a temp DB, and `RELAY_TIME_SCALE=1000`.
- Live smoke tests are marked `@pytest.mark.live` and skipped unless `-m live` is passed and keys exist.
- Each scenario has an end-to-end test that runs its replay in mock mode and asserts the `PlayLog` sequence (for example, C produces exactly 7 plays).

**Frontend**
- One `api.ts` with typed fetch helpers. `types.ts` mirrors backend DTOs by hand (small surface, no codegen).
- Tailwind utility classes only. No component library.
- The header always shows `LLM: live|mock · TTS: live|mock` from `GET /health`.

**Git**
- Small commits per phase step. Never commit `.env`, `data/`, `node_modules/` or generated audio, except the real ElevenLabs samples in `docs/samples/` (user decision, Phase 8: paid for once, listed in `manifest.json`, seeded into the cache so the demo replays them at zero cost).

## Definition of done (every change)

`make lint` and `make test` pass. If behaviour changed, the relevant doc is updated. The mock-mode demo for the affected scenario still runs.
