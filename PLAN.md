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
- `GET /playback/stream?zone=…` (SSE) on top of `bus.py` (built in Phase 2 for `/ws/events`).
- `sources/replay.py` and `POST /scenarios/{name}/run` already exist (Phase 2); here they start driving the scheduler.
- Frontend Zones page: one panel per zone with an `EventSource`, a sequential `<audio>` queue, a now-playing caption, and the next play time.
- **Tests (mock, `RELAY_TIME_SCALE=1000`):**
  - **A:** the severe message stops after the update, and the good-service message plays exactly 2×.
  - **B:** exactly 3 plays per zone at urgency levels 1, 2, 3. Resolving after play 1 stops it.
  - **C:** exactly 7 plays, then auto-resolve.
  - Priority: a page preempts the queued info message in a shared zone, and nothing is cut mid-clip.
- **Exit:** all three scenarios run end to end in the browser in mock mode, time-compressed. **This is the first fully demoable build.**

## Phase 6: Operator console

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
