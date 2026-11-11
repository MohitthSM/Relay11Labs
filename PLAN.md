# Relay: Phased Build Plan

Phase 1 (this set of docs) is done. Each later phase ends with `make lint && make test` passing and a mock-mode demo that still works. Phases are ordered so the demo is runnable **from Phase 5 onward** without any API keys.

---

## Phase 2: Scaffold and events

> **Status:** built in mock mode. Done: `models.py` (`Event`, `LLMCall`, `Announcement`, `PlaybackJob`, `PlayLog`, plus the voice tables), `events.py` (create, versioned update, resolve), `api/events.py` (`POST /events`, `PUT /events/{key}`, `POST /events/{id}/resolve`, `GET /events`, `GET /events/{id}`), `GET /health` per provider, the Vite/React/TS/Tailwind scaffold with a console listing events (announcement cards with audio players, scenario buttons and a speed selector). **Scope added by the Phase 2 brief:** the `/ws/events` WebSocket for state changes (amends D8), the mock scenario feed for A, B and C with speed control (`POST /scenarios/{name}/run`, `PUT /speed`; pulled forward from Phase 5), and the end-to-end wiring event -> script engine -> `voice.render_many`, so scenario B goes from `POST /events` to nine playable clips. **Not built:** a `Zone` table (zones are YAML config, D12; the brief's "Zone" table would contradict that), the event form (Phase 6). The workflow framework that was built first was removed as out of scope, with the chat and CRM providers (ARCHITECTURE §10 is gone).

**Goal:** a running FastAPI app that stores versioned, validated events.

- **Backend**
  - Repo skeleton exactly as in CLAUDE.md.
  - `pyproject.toml` (uv), ruff and pytest config. `Makefile`, `.gitignore`, `.env.example`.
  - `config.py` (Settings, mock/live resolution), `db.py`, `models.py` (all tables, even unused ones), `timeutil.py`.
  - `schemas.py`: `LinePayload`, `PassengerPagePayload`, `StandChangePayload` with `fact` field metadata, plus the discriminated union.
  - `sites.py` and three site YAMLs (tube station, airport, bus station) with zones, languages and repeat policies.
  - `events.py`: `create`, `update` (version bump, previous row → `superseded`) and `resolve`. No scripts yet.
  - `api/events.py`: `POST /events`, `GET /events`, `GET /events/{id}`, `POST /events/{id}/resolve`. Plus `GET /health`, which reports mock/live per provider.
- **Frontend:** Vite + React + TS + Tailwind scaffold. Header with health status, and a Console page listing events.
- **Tests:**
  - payload validation, including a bad flight number, an unknown status and a missing gate
  - versioning: an update supersedes the previous version, and resolve sets the state
  - health shows mock when no keys are set
- **Exit:** `curl` the 3 scenario events and see them versioned in the console.

## Phase 3: Script engine

> **Status:** built in mock mode. Done: `schemas.py` and `sites.py` (pulled forward from Phase 2), three site YAMLs, providers (`llm_mock`, `llm_anthropic`), templates, the fact lock (5 layers + format, required claims, urgency, filled-facts), lexicons in EN/ES/FR, prompt files per hub type (D15), `engine.py` returning a `ScriptPlan`, staff notes with `POST /events/from-note`, `normalize.py`, scenario JSONs. `LLMCall`/`Announcement` rows are now written by `events.py` (Phase 2), and the console shows an announcement card per slot. **Still open:** the `-m live` smoke run (no API key in the build environment; see ARCHITECTURE §9).

**Goal:** events become fact-locked scripts in EN/ES/FR at each urgency level, with model routing logged.

- `providers/llm_base.py`, `llm_mock.py` (with `RELAY_MOCK_HALLUCINATE`), `llm_anthropic.py` (`messages.parse`, effort `low`, 6 s timeout, `stop_reason` handling), `pricing.py`.
- `script/templates.py`: every type × urgency × language.
- `script/factlock.py`: the 5 layers from ARCHITECTURE §4. Lexicon YAMLs for claims and number words in EN/ES/FR. Gazetteer from site YAML.
- `script/prompt.py`: house style from the site, rules, the placeholder list generated from schema metadata, and `PROMPT_VERSION`.
- `script/engine.py`:
  1. one batched call on `SCRIPT_MODEL_DEFAULT`
  2. per-slot fact lock
  3. failed slots retried on `SCRIPT_MODEL_ESCALATION` with the violations appended
  4. any slot still failing gets its template
  5. `LLMCall` and `Announcement` rows written
- Staff notes: `extract_event` on the escalation model, plus the extraction lock (every value must appear in the note). `POST /events/from-note` returns the draft event for confirmation and does not create it.
- `script/normalize.py`: code spacing and time phrasing.
- Verify that structured outputs work on `claude-haiku-5-5`. If they don't, switch to JSON-in-text with Pydantic validation and record the change in ARCHITECTURE §9.
- Console: an announcement card per language/urgency showing the script, the source badge (LLM/template) and rejection reasons.
- **Tests:**
  - ≥ 40 fact-lock cases, positive and negative, in all three languages
  - routing: a hallucinating mock leads to an attempt-2 call on the escalation model, then a template
  - staff note extraction drops values not found in the note
  - templates exist for every slot, which is a generated test
- **Exit:** with mock and hallucinate on, scenario B shows a rejected FR/3 script with reasons, and the template takes its place. One `-m live` smoke test against Claude passes.

## Phase 4: Voice layer

> **Status:** built and tested in mock mode. Done: `tts_base`/`tts_mock`/`tts_elevenlabs` (`sync_dictionary` too), `build_tts`, `models.py` (`AudioClip`, `VoiceRender`), `voice.py` (routing D17, cache, segments and stitching D18, metrics, `stats()`), `pronunciation.py`, `pronsync.py`, `scripts/sync_pronunciations.py`, `static/chime.wav`, `GET /audio/{clip_id}`, tests. **Deviations from the list below:** the dictionary is created with `create_from_rules` (PLS parsed locally) and its id and version go to `sites/pronunciation.lock.json`, not into the site YAML, so YAML comments survive. The chime is WAV and is stitched into the first-language clip. **Still open:** the `-m live` smoke test has never run (no key in the build environment); the default voice is a placeholder taken from the API docs and nobody has listened to it in EN/ES/FR; the `eleven_v4_turbo` vs `eleven_v4` comparison; the IPA in `sites/*.pls` is unverified by ear; `sync_pronunciations.py` has never run against the real API; (`voice.render_many` is now called by `events.py`, and the console cards have a play button.)

**Goal:** approved scripts become cached audio clips.

- `providers/tts_base.py`, `tts_mock.py` (stdlib `wave`, duration proportional to the text), `tts_elevenlabs.py` (`text_to_speech.convert` with `model_id`, `language_code`, `output_format`, `seed` and `pronunciation_dictionary_locators`).
- `voice.py`:
  - content-hash cache
  - semaphore set to `ELEVENLABS_MAX_CONCURRENCY`
  - `asyncio.to_thread` for renders
  - `mutagen` for duration
  - `AudioClip` rows with characters and cost
- `scripts/sync_pronunciations.py`: uploads `sites/*.pls` with `pronunciation_dictionaries.create_from_file` and writes the id and version back to the site YAML. The starter PLS covers Southwark, Marylebone, Holborn and Canary Wharf.
- `api/playback.py`: `GET /audio/{hash}` (static file). Console cards get a play button.
- Choose one default voice per site and listen in EN/ES/FR on `eleven_v4_turbo` to confirm one voice works across languages. Compare against `eleven_v4` and note the result in ARCHITECTURE §7.
- **Tests:**
  - a cache hit makes no provider call
  - the hash changes when voice, model or text changes
  - the mock WAV duration matches the formula
  - a live smoke test renders 1 clip
- **Exit:** scenario B renders 9 clips. Re-running it renders 0 new clips, and the hit count rises.

## Phase 5: Scheduler

> **Status:** built and tested in mock mode. Done: `scheduler.py` (`step(ctx, state, now)` as a sync, clock-free tick plus the `run` loop in the lifespan), `jobs.py` (one job per event version and zone, approval rule, hold/release/cancel), per-zone queues with priority preemption at clip boundaries and resume (D19), never-stack repeats, escalation, `max_plays`, expiry and auto-resolve, resolution messages, the `PlayLog` audit log (`GET /playback/log`), `GET /playback/zones`, deduplication of identical events (D21). **Scope added by the Phase 5 brief:** human approval before first play for staff notes and high-severity events, configurable per site policy (`POST /events/{id}/approve`, D20; scenario runs take `auto_approve`); priority classes security > safety > paging > delays > info (security and safety reserved, CLAUDE.md constraint 5). **Changed from the list below:** play commands go out on the existing `/ws/events` WebSocket, not an SSE stream (D8 amended); the Zones page has one WebSocket for all panels. Tests: `tests/test_scheduler.py` (fake clock) and end-to-end A, B, C in `tests/test_scenarios.py`. **Not verified:** audible playback in a real browser tab (headless check only: the panels, captions and approve flow were exercised with Playwright).

**Goal:** clips play in zones on the right cadence, escalate, and stop correctly.

- `scheduler.py`:
  - 1 s tick
  - due-job query
  - per-zone `busy_until` and priority ordering
  - sequence building: `[lang1, lang2, …]`; the chime is already stitched into the first-language clip (D18, SPEC F1 step 6)
  - escalation (`urgency = min(plays_done + 1, 3)`)
  - `max_plays` and `ends_at`
  - cancellation on update or resolve
  - resolution messages
  - auto-resolve when `ends_at` passes
- ~~`GET /playback/stream?zone=…` (SSE)~~ play commands on the `/ws/events` WebSocket through `bus.py` (D8, amended in Phase 5).
- `sources/replay.py` and `POST /scenarios/{name}/run` already exist (Phase 2); here they start driving the scheduler.
- Frontend Zones page: one panel per zone fed from the page's WebSocket, a sequential `<audio>` queue, a now-playing caption, and the next play time.
- **Tests (mock, `RELAY_TIME_SCALE=1000`):**
  - **A:** the severe message stops after the update, and the good-service message plays exactly 2×.
  - **B:** exactly 3 plays per zone at urgency levels 1, 2, 3. Resolving after play 1 stops it.
  - **C:** exactly 7 plays, then auto-resolve.
  - Priority: a page preempts the queued info message in a shared zone, and nothing is cut mid-clip.
- **Exit:** all three scenarios run end to end in the browser in mock mode, time-compressed. **This is the first fully demoable build.**

## Phase 6: Operator console

> **Status:** built and clicked through in a real browser in mock mode (scenarios A, B and C, the staff-note and form flows, edit, stop, approve, and a backend restart). Done: a dark control-room console with a **live board** (status pending / playing / scheduled / played / resolved, next play countdown, scripts per language and urgency level with source and fact-lock result, **Approve / Edit / Stop**), a **zone map** (a schematic per site that lights while a clip plays, with in-browser playback, one `<audio>` per zone that keeps playing across pages), a **Compose** panel (staff note box, one form per event type, script and fact-check preview, Broadcast, D22), **Metrics** (`GET /metrics/summary`, `GET /metrics/announcements`: announcements generated, cache hit rate, characters and cost saved, median and p95 time to audio, LLM and voice model routing, fact-lock rejection by layer, a per-announcement table with no names), a scenario toolbar (run A, B, C, speed, auto-approve, and a hallucinate toggle in mock mode only: `PUT /demo/hallucinate`), `GET /sites`, `POST /events/preview`, `GET /events?detail=true`. **Transport (user decision):** all real-time updates, playback included, use the one `/ws/events` WebSocket and there is no SSE; D8 already said so since Phase 5 and now records that the client reconnects by itself. **Scope changes from the list below:** one console for both roles instead of a station operator view and a gate agent view (the site selector picks the form); **Update** is **Edit** (it opens the form on the event; the fields that name the event are locked); **Stop** is the old Resolve / Passenger boarded (two clicks, to avoid a misclick; a line then plays its good-service message). **Not built:** the inspector does not show the spoken text (after `normalize.py`) or highlight the offending span inside the script (the span is quoted next to the reason); extraction calls for staff notes are not logged as `LLMCall` rows (the draft has no event yet), so they are not in the routing split; previews are not in the metrics either (D22); no frontend test runner, only `npm run typecheck` plus the browser click-through. **Not verified:** audible playback on a real speaker (in headless Chromium, 3 zones gave 3 `playing` and 3 `ended` events with the position reaching the end of the clip); live mode (no keys).

**Goal:** the two user roles can run their jobs without curl, and the metrics page exists.

- **Station operator view:**
  - active events, grouped by site
  - a line-status form (line, status, reason, location)
  - a stand-change form
  - per-event **Update** and **Resolve**
  - a "playing now / next" strip per zone
- **Gate agent view:**
  - a passenger-page form with three fields
  - a staff-note box → extracted draft with "not found in note" warnings → **Broadcast**
  - a big **Passenger boarded** button
- **Announcement inspector:** for each language and urgency, shows the script, the spoken text, the source, the model, the rejection reasons with highlighted offending spans, and the audio.
- **Metrics page** (`GET /metrics/summary`, `GET /metrics/announcements`):
  - model routing share (default vs escalation)
  - tokens and cost per model
  - fact-lock rejection rate by layer
  - template fallback rate
  - TTS characters, cost and cache hit rate
  - p50/p95 time from event to first play
  - per-announcement table with no passenger names
- Demo controls: a scenario runner, a time-scale selector, and a hallucinate toggle (shown in mock mode only).
- **Tests:** API tests for the metrics aggregation. Frontend `npm run typecheck`.
- **Exit:** someone who has not seen the code can run scenario B from the gate-agent view and explain the metrics page.

## Phase 7: Real data (TfL)

> **Status:** built and tested in mock mode, and run once against the real TfL API (Jubilee showed minor delays, an event was created, the console listed it; the failure path was also checked in the browser with the network cut). Done: `sources/tfl.py` (poll, parse, diff, back off; D23), `sources/flights.py` (`FlightFeed` Protocol, `MockFlightFeed`, `ExampleRealFlightFeed` stub, `build_flight_feed`), `api/source.py` (`GET/PUT /source`, `GET /flights`), a Demo / Live toggle in the console toolbar with a feed status chip and a warning banner, TfL fixtures (good, minor with reason, severe with reason, part suspended, planned closure, malformed response, malformed line) and `tests/test_tfl.py`. **Transport (user decision):** no SSE; the feed status travels on the existing `/ws/events` WebSocket (D8 amended). **Changes from the list below:** the events are the existing `line_status` type (create / update / resolve), not new `line_delay` / `line_resumed` types; the raw response is not stored on the event; polling is on only in live mode (`RELAY_DATA_MODE`, default `demo`, switchable at runtime); live mode refuses scenario runs. **Verified against the live API (2026-11-11):** `GET /Line/Mode/tube/Status` returns a list of `{id, name, lineStatuses[{statusSeverity, statusSeverityDescription, reason?}]}`; severities from `GET /Line/Meta/Severity` (2 Suspended, 3 Part Suspended, 6 Severe Delays, 9 Minor Delays, 10 Good Service). The per-line `modified` field was found stale on live data (a September date), so freshness uses our own fetch time. **Not verified:** a long soak (hours) against the real API; behaviour under a real 429 (covered by a mocked transport only); with an app key (none available); audible playback.

**Goal:** scenario A can be driven by live TfL status.

- Allow the host `api.tfl.gov.uk` in the environment network settings.
- `sources/tfl.py`:
  - polls the TfL Unified API line-status endpoint for configured lines every 60 s (not time-scaled)
  - maps TfL status severity and reason text onto `LinePayload` (status enum, `reason` via the claim lexicon, `location` only if it matches the gazetteer)
  - diffs against the current event to call `create`, `update` or `resolve`
  - stores the raw TfL response on the event for traceability
- Verify the endpoint and response shape against the current TfL API docs before coding. Do not rely on memory.
- Optional `TFL_APP_KEY`. Back off on 429 and 5xx errors. If the poller fails, it logs and keeps the last state, and never invents a resolve.
- Recorded TfL fixtures for tests: good, severe with a reason, part-suspended, and malformed.
- **Exit:** with `RELAY_TFL_LINES=jubilee`, the console shows the real current Jubilee status. Scenario A replay is still available for the stage.

## Phase 8: Polish

> **Status:** the review, docs and packaging are done and green in mock mode; **every live check is blocked** because no ElevenLabs or Anthropic key was available to the build session (none in the environment, and the key request was declined). Nothing below claims a live result.
>
> **Done:**
> - **Source switch, both ways** (D23): Live → Demo withdraws TfL events, so they never play over a scenario. Demo → Live stops a running scenario and withdraws its events. A lock makes each switch and a poll or step in flight mutually exclusive. Tests: `tests/test_source_switch.py`.
> - **Code review**, findings and fixes in `docs/REVIEW.md`: the R11 PII purge (`relay/purge.py`), announcement → `LLMCall` linked by attempt, one failed clip no longer fails the whole event, fixed segments warmed at start-up with live TTS, dead code removed, type ignores removed.
> - **Docs and packaging:** README (problem, quickstart, Mermaid diagram, decisions, roadmap), `docs/COSTS.md` (a simulated busy day, `scripts/simulate_day.py`), and `docker compose up --build` (built and clicked through: console, WebSocket, scenario B).
> - **Tools for the live checks**, each one command once a key exists:
>   - `scripts/pick_voice.py`: lists the account's voices (GET /v2/voices) and ranks them for PA use.
>   - `scripts/render_samples.py`: scenario B (9 clips) and A (3 versions) as MP3s in `docs/samples/` with a per-clip report (model, characters, cost, TTFA, total), plus the same B script on `eleven_v4_turbo` and `eleven_v4`.
>
> **Live verification, still open (needs keys):**
> 1. `cd backend && uv run pytest -m live -q` (Claude and ElevenLabs smoke tests). Not run.
> 2. `make sync-pronunciations`, then commit `sites/pronunciation.lock.json`. Not run, so there is no lock file and requests carry no dictionary yet.
> 3. `uv run python scripts/render_samples.py`, then commit `docs/samples/`. Not run. TTFA, characters and cost per clip are unmeasured. The characters are known from mock (see `docs/COSTS.md`); cost follows from the price table.
> 4. Listen to `docs/samples/compare_*` and record v4 Turbo vs v4 in ARCHITECTURE §7. Not done.
> 5. `uv run python scripts/pick_voice.py`, listen in EN/ES/FR, then set `voice_id` in `sites/*.yaml`. Not done: the placeholder `JBFqnCBsd6RMkjVDRZzb` stays. The ElevenLabs docs say the default voices are retired on 2026-12-31, so pick a non-legacy voice.
>
> **Not done from the list below:** `audio_effects`, chime loudness matching, `passenger_name_ipa`, v4 audio tags for level 3, and `speechSynthesis` in mock mode. All of them need listening with live audio, except the last, which was left out to keep mock mode deterministic. The ffmpeg layer of the backend image could not be built in the build environment (`deb.debian.org` is blocked there); it is a standard Debian package.

**Goal:** make the pitch land.

- **Audio:**
  - `audio_effects`: try an environment preset (hall or station) and a PA-like filter preset from the audio effects catalog
  - loudness-match the chime to speech
  - make effects a site-level setting
- **Pronunciation:** an optional `passenger_name_ipa` on pages, inserted by `normalize.py` as inline `/…/` IPA (v4 only). This is never accepted from LLM output.
- **Urgency delivery:** try v4 audio tags inserted by code for level 3. Keep them only if they sound right in all three languages.
- **Mock audio:** optionally use the browser `speechSynthesis` reading of the caption in mock mode, so the demo is audible without keys.
- **PII purge job** (R11) and a README "run the demo in 60 seconds" section.
- **Pitch script:** a 5-minute walkthrough:
  1. A: an update replaces the old message
  2. B: multilingual, escalating, with a fact-lock rejection shown live
  3. C: 7 plays from 1 render
  4. the metrics page
- **Final checks:**
  - full run cost compared with ARCHITECTURE §8
  - cold-start time to first play under 5 s live
  - mock run with the network disabled
- **Exit:** the pitch walkthrough runs twice in a row, live and in mock, with no manual fixes.

---

## Open items to resolve during the build
- Default voice id per site: chosen by listening in Phase 4. Prefer a default voice, since the voice library is unavailable via the API on the free tier.
- ElevenLabs plan for the pitch: Starter or above, for commercial rights.
- Whether `eleven_v4_turbo` or `eleven_v4` sounds better for the hall-reverb PA style. Decide in Phase 8 by listening.
