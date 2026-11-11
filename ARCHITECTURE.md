# Relay: Architecture

Read SPEC.md first. This document covers the components, the data model, the path of one event, and the key decisions with their tradeoffs.

## 1. Shape of the system

```
                ┌──────────────── backend (one FastAPI process) ───────────────────┐
 Sources        │                                                                   │
 ─────────      │  api/events ─► events service ─► script engine ─► voice layer ─►  │
 console form ──┤      ▲              │ (versioning)   │ LLM provider   │ TTS prov.  │
 staff note  ───┤      │              │                │ fact lock      │ audio cache│
 TfL poller  ───┤  sources/          ▼                │ templates      ▼            │
 replay file ───┤               SQLite (SQLModel) ◄────┴──────────── scheduler      │
                │                                                    │ (1 s tick)    │
                │                     api/ws (WebSocket) ◄── bus ◄───┘               │
                └───────────────────────────────┬───────────────────────────────────┘
                                                │ WS state changes + "play" commands; /audio/*
                ┌───────────────────────────────▼───────────────────────────────────┐
                │ frontend (React): Console · Zones (audio players) · Metrics       │
                └───────────────────────────────────────────────────────────────────┘
```

Everything runs in one process with one SQLite file. There are no queues, no Redis and no Celery. The scheduler is a single asyncio task started in FastAPI's `lifespan`.

## 2. Components

| Component | Module | Responsibility |
|---|---|---|
| Config | `relay/config.py` | `pydantic-settings` reads `.env`. Picks mock or live per provider: live only if the key is present **and** the mode is not forced to `mock`. |
| Sites | `relay/sites.py`, `backend/sites/*.yaml` | Static config for each site: zones, languages, voice, house style, the event-type → zones routing, the repeat policy and the gazetteer (known stations, lines, gates, stands). Loaded once at startup. |
| Event schemas | `relay/schemas.py` | Pydantic v2 models, one per event type (a discriminated union on `type`). The single definition of what a "fact" is. |
| Events service | `relay/events.py` | `create`, `update` (version bump, previous row superseded, its jobs cancelled), `resolve` (jobs cancelled; a line gets a generated good-service message, SPEC F4) and `approve` (D20). An event identical to an active one is a duplicate and changes nothing (D21). Each call runs the script engine, writes the `LLMCall` and `Announcement` rows, calls `voice.render_many`, creates the playback jobs (`jobs.py`) and publishes a state change. Errors are `EventError` with an HTTP status. |
| Playback jobs | `relay/jobs.py` | One `PlaybackJob` per (event version, zone). `approval_reason` (pure) decides whether an event waits for an operator (D20); held jobs have no expiry until `release_held`. `cancel_jobs` on update and resolve. Sync, inside the events service's transaction. |
| Script engine | `relay/script/engine.py` | For each language × urgency level: calls the LLM, runs the fact lock, retries once on the escalation model, then falls back to the template. Input: the event plus a `ScriptContext` (site with hub type and style guide, zone languages, previous announcements, repeat count). Output: a `ScriptPlan` (script per language and urgency, priority, repeat interval, expiry, facts used, LLM call records). It writes nothing; the events service persists the plan as `LLMCall` and `Announcement` rows. |
| Fact lock | `relay/script/factlock.py` | A pure function `check(script, event, site, lang) -> list[Violation]`. No I/O. Heavily unit-tested. |
| Templates | `relay/script/templates.py` | Deterministic wording per (event type, urgency, language). Uses `str.format` with the same placeholders as the LLM output. |
| Prompt | `relay/script/prompt.py`, `backend/prompts/*.vN.md` | System prompts are versioned files (D15): one per hub type (`script_underground`, `script_airport`, `script_bus`) with the site's house style and few-shot examples, plus shared `script_rules`. `prompt.py` loads them and builds the user message (claim facts as enum values, value facts as placeholder names only). |
| Staff notes | `relay/script/notes.py`, `note_rules.py` | Note → draft event for a human to confirm. Screens injection and out-of-scope notes, extracts on the escalation model, applies the extraction lock (§4), falls back to deterministic rules if the model fails. |
| Normaliser | `relay/script/normalize.py` | Turns an approved script into spoken text. Spaces out codes (`BA117` → `B A 1 1 7`) and applies per-language time phrasing. Runs **after** the fact lock. |
| LLM providers | `relay/providers/llm_*.py` | A `LLMProvider` Protocol with an `AnthropicProvider` and a `MockLLMProvider`. |
| TTS providers | `relay/providers/tts_*.py` | A `TTSProvider` Protocol with an `ElevenLabsProvider` (wraps the SDK: `convert`, pronunciation dictionaries, error mapping) and a `MockTTSProvider`. Both expose `synthesize` and `sync_dictionary`. |
| Pricing | `relay/providers/pricing.py` | Price table: USD per MTok per Claude model and USD per 1K characters per ElevenLabs model. Used only for logging and metrics. |
| Voice layer | `relay/voice.py` | Routes each announcement to a model (D17), splits off the fixed opening and closing (D18), looks every piece up in the content-hash cache, generates only what is missing (worker threads, capped by `ELEVENLABS_MAX_CONCURRENCY`), stitches chime + segments with pydub, and writes one `VoiceRender` metrics row per announcement. `voice.stats()` aggregates them. |
| Pronunciation | `relay/pronunciation.py`, `relay/pronsync.py`, `scripts/sync_pronunciations.py`, `sites/*.pls`, `sites/pronunciation.lock.json` | The PLS file is the source of truth. The script creates or updates the ElevenLabs dictionary and writes its id and version to the lock file. `sites.py` attaches that version to the site, so every TTS request carries it. |
| Scheduler | `relay/scheduler.py` | `step(ctx, state, now)` is one tick, sync and clock-free: drop plays of cancelled jobs or ended events, put due jobs' plays in their zone's queue (never stacking a job's repeats), expire jobs past `ends_at` and auto-resolve their events, then let each free zone air one clip of its best play (priority, then age; D19), writing the `PlayLog` audit row and publishing a `play` message. `run` is the asyncio loop that calls `step` in a thread every tick, or as soon as a clip ends. Zone state is in memory (D9). |
| Bus | `relay/bus.py` | In-memory fan-out to `/ws/events` WebSocket clients: event state changes and play commands (D8). Each subscriber gets an `asyncio.Queue`; `publish` is safe to call from the threadpool. |
| Sources | `relay/sources/replay.py`, `relay/sources/tfl.py` | The scenario replayer (Phase 2) and the TfL line-status poller (Phase 7). Both just call the events service. The replayer waits through `timeutil`, so the speed control scales it; a replayed `create` on a still-active event becomes an update. |
| API | `relay/api/*.py` | FastAPI routers: `events` (CRUD, versioned update, resolve, staff-note draft), `ws` (the one real-time channel: state changes and play commands), `playback` (audio files, `GET /playback/zones`, the `GET /playback/log` audit log), `scenarios` (run A/B/C, speed control), `metrics` (Phase 6). `GET /health` reports `llm` and `tts` as `mock` or `live`. |
| Frontend | `frontend/src/` | Console (event list, approve, form, staff note, announcement inspector), Zones (one panel per zone with an `<audio>` queue fed from the page's one WebSocket), Metrics. |

### Provider interfaces (the only seams for external services)

```python
class LLMProvider(Protocol):
    def generate_scripts(self, req: ScriptRequest, model: str) -> LLMResult: ...   # ScriptBatch JSON
    def extract_event(self, req: ExtractRequest, model: str) -> LLMResult: ...     # NoteExtraction JSON

class TTSProvider(Protocol):
    def synthesize(self, req: RenderRequest) -> TTSResult: ...   # bytes + billed characters + ttfb/total ms
    def sync_dictionary(self, name, rules, existing_id) -> DictionarySync: ...  # create or update
```

`LLMResult` carries the output as JSON text (validated by the caller against the same Pydantic model) plus `model`, `input_tokens`, `output_tokens`, `latency_ms` and `stop_reason`. It is **all** that the engine sees, so the engine cannot tell whether it is talking to the mock or to the real provider.

- **`MockLLMProvider`.** Returns the reference template for each slot, so the fact lock is exercised realistically, and extracts staff notes with the deterministic rules in `note_rules.py`. Reports fake token counts. Tests can queue exact responses. With `RELAY_MOCK_HALLUCINATE=true` it injects one invented fact per event (a swapped gate number, an invented cause, or "baggage will be offloaded") for the on-stage fact-lock demo.
- **`MockTTSProvider`.** Uses only the stdlib `wave` module to write a WAV: a short two-tone sweep, then quiet tone for about 60 ms per character of text. The duration is realistic, and the console shows the script as a caption.

## 3. Data model (SQLModel, SQLite)

Sites and zones are **config, not tables**. They live in YAML and are referenced by string id.

```
Event
  id: int PK
  event_key: str  (index)    # e.g. "jubilee-status", "page-BA117-smith"; stable across versions
  site_id: str
  type: str                  # line_status | passenger_page | stand_change
  version: int               # 1, 2, 3 … one row per version
  payload: JSON              # validated by schemas.py for `type`; the ONLY source of facts
  source: str                # form | staff_note | tfl | replay | api
  staff_note: str | None     # original note text if source == staff_note
  state: str                 # active | superseded | resolved
  priority: int              # rank of the policy's priority class (D19)
  approval: auto | pending | approved, approval_reason, approved_by, approved_at   (D20)
  created_at, resolved_at

LLMCall                      # one row per API attempt; feeds the metrics page
  id, event_id FK, purpose (script | extract), attempt (1 | 2)
  model, input_tokens, output_tokens, cost_usd, latency_ms
  outcome: accepted | rejected | timeout | error | refused
  violations: JSON           # fact-lock reasons when rejected

Announcement                 # one per (event version, language, urgency level)
  id, event_id FK, language, urgency (1..3), is_resolution: bool
  draft: str                 # as written, with {placeholders} (never contains a name)
  script: str                # with placeholders filled, as checked by the fact lock
  model: str | None, rejections: JSON, audio_error: str | None
  spoken_text: str           # after normalize.py; what is sent to TTS
  source: llm | template
  llm_call_id FK | None      # which call produced the accepted script
  clip_id FK -> AudioClip

AudioClip                    # content-addressed cache entry
  id: str PK                 # sha256 of provider + every RenderRequest field (voice.cache_key)
  path, kind (segment | announcement), permanent: bool   # permanent = fixed opening/closing
  provider (elevenlabs | mock), model_id, voice_id, language
  characters, cost_usd, duration_s, created_at, hits: int
  parts: JSON                # for a stitched announcement: the segment clip ids

VoiceRender                  # one row per announcement voiced, cache hit or not (R7); no text
  id, event_id, announcement_id (plain ints, no FK yet), language, urgency
  kind (live | scheduled), model_id, provider, clip_id, cache_hit
  segments_total, segments_cached
  characters_total, characters_billed, characters_saved, cost_usd, cost_saved_usd
  ttfa_ms, total_ms, created_at

PlaybackJob                  # "play this event's sequence in this zone on this cadence"
  id, event_id FK, zone_id, languages: JSON (zone play order), is_resolution
  next_at: datetime, interval_s: int, plays_done: int, max_plays: int | None
  duration_s, ends_at | None # ends_at = release time + duration (a held job has none yet)
  escalate: bool             # if true, play urgency = min(plays_done + 1, 3)
  priority, state: held | scheduled | done | cancelled

PlayLog                      # the audit log: one row per play (a sequence) in one zone; no text
  id, job_id FK, event_id FK, event_version, zone_id, urgency, is_resolution, priority
  announcement_ids: JSON, scripts: JSON   # per announcement: source, model, prompt_version
  approved_by, approved_at   # an operator, "auto" or "scenario runner"
  played_at, finished_at, clips_played, interruptions, status: playing | played | stopped
```

Why one `Event` row per version rather than an event table plus a history table: it is one table and one rule ("the latest active row with this key is current"), and old versions stay inspectable in the console.

## 4. The fact lock

**Rule:** a generated script may contain only facts that are present in the event. If any check fails, the script for that language is rejected. Relay retries once on the escalation model with the violations as feedback. If that also fails, it uses the template.

The LLM never writes facts as text. It writes **placeholders**, and code fills them from the event payload:

```
LLM output (fr):  "Dernier appel pour {passenger_name}, passager du vol {flight_number} à destination de {destination}. Veuillez vous présenter immédiatement à la porte {gate}."
```

`check()` runs the five layers below plus three more checks (format, urgency, filled facts). Each is a small, separately tested function, and each violation names its layer, a code and the offending span.

| # | Layer | Rejects when |
|---|---|---|
| 1 | Placeholders | A `{name}` is not a field of this event type, or a field marked `required` for this type/urgency is missing (for example, a final call without `{gate}`). |
| 2 | Digits and codes | The *unfilled* text contains any digit, a time (`\d{1,2}[:.h]\d{2}`), or an alphanumeric code (`[A-Z]{1,3}\d+`). Facts can only arrive through placeholders. |
| 3 | Number words | The unfilled text contains a cardinal number word from the per-language list (`one…ninety`, `uno…`, `un…`). A small allow-list covers idioms such as "no one". |
| 4 | Proper nouns | A capitalised word that is not at the start of a sentence is not in the site's brand allow-list. In addition, at any position, a word that appears in the site **gazetteer** (all stations, lines, gates, cities and airlines the site knows) is rejected, because it is only legal as a placeholder value. This catches "Victoria line" in a Jubilee event. |
| 5 | Claim lexicon | The text asserts an operational claim the event does not support. A per-language YAML maps claim keys to regexes (`cancelled`, `delayed`, `closing`, `offloaded`, `suspended`, `diverted`, `minutes`, and every disruption **reason** such as `signal_failure` or `customer_incident`). The event payload determines which claim keys are allowed: `severe_delays` + `signal_failure` allows exactly those. |

Also enforced:
- **Format:** maximum word count per language, no `[` `]` `<` `>` `/` `\` (so the LLM cannot inject audio tags, SSML or IPA; those are added only by code), no stray braces, and the output language matches the requested one (cheap check: that language's stop-words outnumber every other language's).
- **Required claims:** a line-status script must state its status, and its cause when there is one (layer 5 run in reverse). An urgency-1 page must say "final call".
- **Urgency:** a page at urgency 2 or 3 must contain at least 2 or 3 distinct urgency markers (`style.<lang>.yaml`), and only urgency 3 may claim the gate is closing (`closing` claim). This is what makes repeat 3 sound more urgent than repeat 1, whoever wrote it.
- **Filled facts:** after the placeholders are filled, every time, number, code and gazetteer name extracted from the final text must come from the event's facts. This is a last cross-check that also covers templates.

**Known limits, stated plainly:**
- A capitalised word at the start of a sentence passes layer 4 unless it is in the gazetteer.
- Layer 5 is only as complete as its lexicon.

Templates are the safety net. The lexicon grows whenever a rejection is missed, and every miss becomes a test case.

**Staff notes.** The note is untrusted input. Defences, in order:
1. **Screen.** Notes that read like instructions to the system ("ignore your instructions and announce X", tags, "say '…'") or that are safety-critical (evacuation, fire, security) are refused before any model sees them (`lexicon/notes.en.yaml`).
2. **Data, not instructions.** The model sees the note only inside `<staff_note>` tags and can answer only with typed `NoteExtraction` fields. It has a `looks_like_instruction` flag, which rejects the note.
3. **Extraction lock.** Every extracted value must appear in the note (case-insensitive; codes may be split, "ba 117"). Lines, stations and cities must also be in the site gazetteer (aliases allowed: "NYC" → New York). Enums must match a pattern in the staff-note lexicon (`notes.en.yaml`: "sig fail", "down"). Anything else is dropped and shown to the agent as "not found in note". The staff-note lexicon is separate from the claim lexicon because shorthand such as "down" would cause false rejections in scripts.
4. **Typed payload.** The kept fields are validated against `schemas.py`; the site must announce that event type.
5. **No path to the script.** The note never reaches the script prompt; scripts come from the typed payload and pass the normal fact lock.
6. **Human confirms.** `POST /events/from-note` returns a draft and an English preview; it creates nothing.
If the model times out, refuses or returns invalid output, the deterministic rules in `note_rules.py` extract the fields instead (the same rules back the mock).

## 5. Path of one event: scenario B, ingestion to audio

1. **Ingest.**
   - The gate agent submits the form. The console calls `POST /events` with `{site_id: "airport", type: "passenger_page", payload: {passenger_name: "John Smith", flight_number: "BA117", destination: "New York", gate: "23", page_type: "final_call"}}`.
   - `schemas.PassengerPage` validates the payload, and a bad flight-number format returns 422.
   - `events.create()` derives `event_key = "page-BA117-john-smith"`, finds no active row, and inserts `Event(version=1, state=active, priority=90)`.
   - Derived facts are added in code. The airline is looked up from the IATA prefix (`BA` → "British Airways") using a small static table.
2. **Plan the scripts.**
   - The site config says `passenger_page` → languages `[en, es, fr]` and urgency levels `[1, 2, 3]`.
   - That is 9 script slots in one LLM call, with structured output `{scripts: [{language, urgency, text}]}`.
3. **Call the LLM.**
   - `engine` calls `llm.generate_scripts(req, model=SCRIPT_MODEL_DEFAULT)` (`claude-haiku-5-5`, effort `low`, 6 s timeout).
   - The Anthropic provider uses `client.messages.parse(..., output_format=ScriptBatch)`, where `ScriptBatch` is a Pydantic model, so `parsed_output` arrives already validated.
   - An `LLMCall` row is logged with its tokens and cost.
4. **Fact lock per slot.**
   - Placeholders are filled from the payload and `check()` runs. Say the 8 slots pass and `fr/3` fails layer 5 because it contains "vos bagages seront débarqués" ("your bags will be offloaded") and the event has no `offload` claim.
5. **Escalate the failures.**
   - Only the failed slots are re-requested from `SCRIPT_MODEL_ESCALATION` (`claude-sonnet-5-5`), with the violation messages appended. This is logged as `LLMCall(attempt=2)`.
   - If it passes, that slot's source is `llm`. If it fails again, or times out, or the model refuses, the slot gets `templates.render("passenger_page", 3, "fr", payload)` and source `template`.
   - If attempt 1 **times out**, every slot gets its template at once with no attempt 2 (R10: the announcement must not wait twice). A refusal, provider error or invalid output on attempt 1 goes to the escalation model like a rejection.
6. **Normalise.** `normalize.py` produces the spoken text. `BA117` becomes `B A 1 1 7`, and the format is consistent across languages. 9 `Announcement` rows are written.
7. **Render the voice.**
   - `voice.render_many()` takes the 9 announcements. Each is routed to a model (D17): level 1 is `live` (`eleven_v4_turbo`), levels 2 and 3 are `scheduled` (`eleven_v4`). A fixed opening or closing is split off (D18; the airport has none, so each clip is one segment). Every piece is looked up by `voice.cache_key()` (provider, model, voice, language, output format, seed, voice settings, pronunciation dictionary version, text). A hit increments `hits` and costs nothing. A miss calls `ElevenLabsProvider.synthesize()`:
     ```python
     client.text_to_speech.convert(
         voice_id=site.voice_id, model_id="eleven_v4_turbo", text=spoken_text,
         language_code="fr", output_format="mp3_44100_128", seed=stable_seed,
         pronunciation_dictionary_locators=[site_dictionary_locator],
     )  # model_id is eleven_v4 for the scheduled (level 2 and 3) clips
     ```
   - Misses run in worker threads, capped by one process-wide semaphore (`ELEVENLABS_MAX_CONCURRENCY`, default 2). The same segment is never generated twice in a batch: the first announcement to need it pays and the others reuse it. Only the calling thread touches the database.
   - The bytes go to `data/audio/<hash>.mp3`, the duration is read with `mutagen`, and an `AudioClip` row is written. When there are several segments or a chime, pydub stitches them (150 ms gaps) into a second `AudioClip`, so a repeat is a single lookup.
   - One `VoiceRender` row per announcement records the model, characters billed and saved, estimated cost, time to first audio and total time.
8. **Schedule.**
   - The site policy for `passenger_page` is: zones `[gate-23, concourse-b]`, interval 120 s, `max_plays=3`, `escalate=true`, priority class `paging`, approval `risk`.
   - A page from the form is routine, so `jobs.approval_reason` returns nothing and the event is `approval=auto`. (A page typed as a staff note, or a severe line status, would be `pending` and its jobs `held` until an operator clicks **Approve**, D20.)
   - Two `PlaybackJob`s are inserted with `next_at=now` and `ends_at = now + duration_s` (scaled).
9. **Tick.**
   - Within 1 s, the scheduler sees both jobs due. It picks urgency level `plays_done + 1 = 1`, builds the sequence `[en/1, es/1, fr/1]` and puts one play in each zone's queue. The chime is already stitched into the front of the first-language clip (D18), so the scheduler adds none.
   - `gate-23` is free, so it airs the play's first clip: it writes the `PlayLog` row (event version, scripts' provenance, approved by `auto`), sets the zone busy for the clip's duration plus a short gap (scaled), and publishes `{type: "play", zone_id: "gate-23", event_id, play_id, announcement_id, language: "en", url: "/audio/<hash>.mp3", duration_s}` on the bus. No caption and no name travel on the socket.
   - It increments `plays_done` and sets `next_at += 120 s / RELAY_TIME_SCALE`. When the en clip ends, the next tick airs es, then fr, unless a higher-priority play is waiting (D19).
10. **Play.**
    - The Zones page holds one WebSocket (`/ws/events`, D8) and hands each `play` command to its zone's panel, which plays the clips in order through one `<audio>` element. The caption comes from `GET /playback/zones`.
11. **Escalate and stop.**
    - Plays 2 and 3 use urgency levels 2 and 3. After play 3, `plays_done == max_plays` and the job becomes `done`; at `ends_at` (360 s) the event auto-resolves.
    - If the agent clicks **Passenger boarded** first, `events.resolve()` marks the event resolved and cancels its jobs. The next tick drops the clips still waiting (the `PlayLog` row ends `stopped`); a clip already on air finishes.

Time from ingest to first play is dominated by step 3 (about 1–2 s on Haiku) and step 7 (about 1 s per clip, 2 in parallel). The target is under 8 s for 9 clips. Mock mode takes under 300 ms.

## 6. Key design decisions and tradeoffs

| # | Decision | Why | Tradeoff accepted |
|---|---|---|---|
| D1 | **The LLM writes placeholders, not facts.** Code fills them. | It makes the fact lock mostly mechanical: no digits, codes or gazetteer names allowed in model text. It also makes the LLM and template outputs the same shape. | Slightly stiffer phrasing. Inflection around names, such as gendered articles in FR/ES, is the model's job and is not checked. |
| D2 | **Fall back to a template per language, not per event.** | A bad French script should not throw away a good English one. | Mixed provenance in one sequence (EN from the LLM, FR from a template). The console labels each one. |
| D3 | **Haiku by default, Sonnet on retry and for staff notes.** (User decision.) | Most events are structured and simple, so the cheap, fast model is enough. The harder cases go to the stronger model. Routing is visible on the metrics page. | Two models to prompt-test. Haiku may be rejected more often, which is acceptable because the rejections are measured. |
| D4 | **Pre-render every urgency level and language at ingest.** | Playback never waits on generation, and escalation costs no latency. | Some clips are never played (if a page is resolved after play 1). That costs a few hundred TTS characters. |
| D5 | **Content-hash audio cache.** | Repeats and identical re-issues cost nothing. Scenario C bills one clip for 7 plays. | The cache can grow, so a PII purge job deletes resolved-page clips (R11). |
| D6 | **ElevenLabs HTTP `convert`, not streaming or WebSocket.** | A PA clip must be complete and approved before it plays. Streaming saves time-to-first-byte, which is irrelevant when the whole clip is queued behind a chime. WebSockets also add connection management. | First play waits for the full render (about 1 s). Switching to `text_to_speech.stream` later is a change inside one provider. |
| D7 | **`eleven_v4_turbo` as the default TTS model.** See §7. **Amended by D17:** it is the `live` model; `scheduled` clips use `eleven_v4`. | $0.04 per 1K characters (Flash pricing), 90+ languages, about 100 ms model latency, IPA phoneme dictionaries in non-English languages, and one voice that is fluent across languages. | No SSML or `<break>` support on v4, so pauses come from punctuation and the chime is a separate file. `eleven_v4` ($0.08) can be switched in by config for higher quality. |
| D8 | **One WebSocket (`/ws/events`) for every real-time update: console state changes and playback commands. No SSE.** (User decision, Phase 5: amends the Phase 2 split, "SSE for play commands, WebSocket for state changes", which itself amended "SSE, not WebSockets".) | One transport, one bus, one client helper. The WebSocket already existed for the console, and play commands are just another message type (`play`); each page filters what it needs. Messages carry ids, urls and state only, so no names travel on it; clients re-fetch details (captions included) over HTTP. | Every client receives every message (the console ignores `play`, the zones page uses both). No automatic reconnect as `EventSource` has; a dropped socket needs a page reload. Playback acknowledgements, if ever needed, would still be plain `POST`s. |
| D9 | **The server owns the zone clock.** `busy_until` is computed from clip durations, scaled by `RELAY_TIME_SCALE` like every other interval (D11). | One authority for ordering and priority. The browser stays a dumb player. Scaling the clip time keeps a compressed scenario's cadence and play counts exact (C: 7 plays in 20 s at 60x). | If a browser tab lags, server and audio drift slightly. Above 1x the server sends clips faster than they can be heard, so the zone player never cuts a clip but keeps only the newest few queued. This is acceptable for a demo, and real PA hardware would report completion. |
| D10 | **asyncio scheduler in-process with a 1 s tick, not APScheduler or Celery.** | One small module that can be explained in a review: the tick is a sync function of `now`, so tests drive it with a fake clock. The loop also wakes when a clip ends, so a zone's languages follow each other without a tick's delay. Jobs live in SQLite, so the next tick after a restart resumes them. | Single process only, which is a non-goal anyway. Zone queues are in memory: a play that was half way through at a restart is lost. Auto-resolving an expired event runs inside the tick. |
| D11 | **`RELAY_TIME_SCALE`** divides every interval. | A 20-minute scenario C runs in 20 s on stage, and tests run instantly. | Every interval must go through one helper (`scaled(seconds)`), which CLAUDE.md enforces. |
| D12 | **Sites as YAML, not DB tables.** | Zones, policies and house style are config that changes rarely and belongs in version control. | Changing them needs a restart. |
| D13 | **Mock mode chosen automatically when a key is missing.** It can also be forced per provider. | "The demo never breaks." A dead key on stage degrades to mock and does not crash. | Live failures could be hidden, so the console header always shows `LLM: live/mock · TTS: live/mock`. |
| D14 | **Retries have one owner per provider kind.** Providers never retry beyond the SDK defaults. The **script engine owns LLM retries**: one retry, on the escalation model, with the violations as feedback, then the template (a timeout skips the retry, R10). **`voice.py` owns TTS retries**: one retry of a retryable `ProviderError` (429, 5xx, timeout) with backoff through `relay/retry.py`, time-scaled. (Amended in Phase 2; it replaces the earlier note about a workflow engine, which was removed as out of scope.) | One place decides what happens on failure, and it is the layer that knows the fallback: a template for text, a failed clip shown as `audio_error` for audio. | Two small retry policies instead of one. A failed render is not hidden: the announcement keeps its text and the API still answers 2xx. |
| D15 | **System prompts are versioned files, one per hub type, with few-shot examples.** (User decision, Phase 3.) Supersedes "prompts as f-strings in `prompt.py`". | Underground, airport and bus announcements have different registers; a file per hub keeps each readable and diffable, and the version ref on every LLM call ties a script to its prompt. | One more place to look. The user message is still built in code from schema metadata. |
| D16 | **Value facts are never sent to the LLM, only their placeholder names.** Previous announcements are passed as placeholder drafts too. | The model cannot copy a value wrongly, and passenger names never leave Relay through the LLM (R11). | The model cannot adjust grammar to a value (gender, elision); D1 already accepts this. |
| D17 | **Model routing is a config table keyed by render kind.** `live` (first play, updates, resolution messages) uses `ELEVENLABS_MODEL_LIVE` (`eleven_v4_turbo`, about 100 ms). `scheduled` (urgency levels 2 and 3, rendered ahead of their play) uses `ELEVENLABS_MODEL_SCHEDULED` (`eleven_v4`, highest quality). `voice.render_kind(urgency, is_resolution)` picks the kind, and the model is logged on every `VoiceRender` row. (User decision, Phase 4.) | Nobody waits on a clip that plays minutes later, so it can use the slower, better model. The model is part of the cache key, so an announcement always maps to one model. | A repeat of the same clip is a cache hit and is **not** re-rendered on the better model: "repeats cost zero characters" wins. `scheduled` is twice the price: scenario B's 9 clips cost about $0.085 instead of $0.05. |
| D18 | **Fixed phrases are voiced once; only the dynamic middle is generated.** Each site lists `segments` per language (`openings`, `closings`). A script that starts or ends with one has it cut off (exact match, on a sentence boundary). Those clips are `permanent`. The chime is a static `static/chime.wav`, stitched in front of the **first language** of a sequence only, with pydub. The stitched announcement is cached as well. | Updates, the same opening on every announcement, and the apology at the end stop costing characters, and the opening sounds identical every time. | Prosody can change at a seam, so the middle is sent with `previous_text` and `next_text` (supported on v4, not v3) and gaps are 150 ms. Only wording that matches a configured phrase exactly is split, so the airport pages have no fixed parts. The chime is WAV so mock mode needs no ffmpeg; live MP3 stitching needs `ffmpeg` installed. |
| D19 | **Priority classes with preemption at clip boundaries, and resume.** Order: `security` > `safety` > `paging` > `delays` > `info` (`sites.PRIORITY`). Site policies may use only `paging`, `delays` and `info`; `security` and `safety` are reserved and rejected by the site loader, because Relay makes no safety-critical announcements (CLAUDE.md constraint 5). Resolution messages are `info`. Each zone has a queue and airs one clip at a time; when it is free it airs the next clip of its best play (highest priority, then oldest). So a page that arrives while an info message is mid-sequence cuts in after the current clip, and the info message resumes with its remaining languages afterwards (`PlayLog.interruptions`). Plays of one job never stack: a repeat due while the previous play has not started is merged into it; one due while it is still airing waits for it. (User decision, Phase 5.) | R8 says clips are never cut mid-play, so preemption happens between clips, never inside one; for one-language zones it means "next in the queue". Resuming keeps every language of a sequence heard. | The resumed languages play without a chime (it is stitched into the first-language clip only, D18). A low-priority zone can be starved while higher-priority plays keep coming; acceptable for pages, which are capped by `max_plays`. A worst-case wait is one clip (about 10 s). |
| D20 | **Human in the loop before first play, configurable per site policy.** `approval: risk` (default): staff-note events and high-severity events (`jobs.HIGH_SEVERITY`: line status severe delays, part suspended, suspended) wait for an operator; routine structured events (pages from the form, stand changes, minor delays) auto-play. `always` and `never` are the other settings. A waiting event's jobs are `held` and its clock does not start; `POST /events/{id}/approve {operator}` releases them, and the operator's name goes on the event and on every `PlayLog` row (`approved_by`: an operator, `auto`, or `scenario runner` for unattended demo runs). An update creates a new version that is judged again. (User decision, Phase 5.) | The risky inputs are free text and severe disruption; routine structured events are already fact-locked, and the gate agent who fills the page form has made the human decision. | "Low-risk templated" is read as "routine structured event": in live mode scripts are LLM-written, so requiring `source == template` would hold almost everything. No authentication, so the operator name is self-declared (SPEC non-goal). An update to a held event cancels the old held jobs, so stale content never plays, but nothing plays until the new version is approved if it is high severity too. |
| D21 | **Replace, never stack; identical events are duplicates.** An update cancels the previous version's jobs (held or scheduled) before the new version's are created, so a zone has at most one job per event key. An event whose site, type and facts equal an active event's is a duplicate: `create` and `update` return the active event unchanged (no new version, scripts, audio or jobs) and the API answers with `duplicate: true` (POST: 200 instead of 201). (User decision, Phase 5.) | A feed or a nervous operator that repeats itself must not double the announcements, and a re-run scenario must not pile up versions. | A duplicate with a different explicit `event_key` returns the existing event under its own key. Comparing payloads is exact: a whitespace or case difference in a free-text field is a new event. |

## 7. ElevenLabs: what the current docs say (checked 2026-11-10; model IDs, prices and the pronunciation and request-stitching endpoints re-checked 2026-11-11)

Sources: elevenlabs.io/docs (models, text-to-speech capability, Eleven v4, best-practices, pronunciation-dictionaries guide, streaming guide, request-stitching guide, latency guide, TTS `convert` API reference, models/concurrency) and elevenlabs.io/pricing/api. PyPI `elevenlabs` latest stable is **2.77.0** (a `3.0.0a1` pre-release also exists).

**Models (TTS):**

| Model ID | Languages | Char limit | Latency† | API price / 1K chars | Notes |
|---|---|---|---|---|---|
| `eleven_v4` | 90+ | 10,000 | n/a | $0.08 | Highest quality. No SSML. Audio tags. Inline IPA (`/…/`). |
| **`eleven_v4_turbo`** | 90+ | n/a | ~100 ms | **$0.04** | Real-time v4. Used with `text_to_speech.convert` in the official pronunciation-dictionary example. |
| `eleven_v3` | 70+ | 5,000 | n/a | $0.08 | Previous generation. No request stitching. |
| `eleven_multilingual_v2` | 29 | 10,000 | n/a | $0.08 | Stable long-form. Does **not** accept `language_code`. |
| `eleven_flash_v2_5` | 32 | 40,000 | ~75–100 ms | $0.04 | Number normalisation is off by default; turning it on is Enterprise-only. |

† Median model latency per the docs, excluding network. `eleven_turbo_v2*` is deprecated in favour of Flash.

**Facts that shaped the design:**
- **One voice, many languages.** On v4, when the target language differs from the voice's language, the output is fluent native speech, not accented speech. One brand voice serves EN/ES/FR. This is a pitch point.
- **Pronunciation dictionaries:**
  - Phase 4 creates them with `client.pronunciation_dictionaries.create_from_rules(name=..., rules=[...])` (the PLS file is parsed locally; `create_from_file` also exists) and updates them in place with `rules.add` (replaces a rule with the same `string_to_replace`) and `rules.remove`. Each write returns a new `version_id`.
  - Passed to a request as `pronunciation_dictionary_locators=[PronunciationDictionaryVersionLocator(pronunciation_dictionary_id, version_id)]`, with at most 3 per request.
  - Phoneme rules (IPA/CMU) work only on `eleven_v4`, `eleven_v4_turbo`, `eleven_flash_v2` and `eleven_v3`. Other models need alias rules.
  - Non-English IPA needs v4.
  - Matching is case-sensitive.
  - Relay keeps one PLS per site (station names such as Marylebone, Southwark, Holborn) and uploads it once with `scripts/sync_pronunciations.py`.
- **`convert` parameters Relay uses:** `language_code` (ISO 639-1), `seed` (best-effort determinism), `output_format` (default `mp3_44100_128`; 192 kbps needs Creator, 44.1 kHz PCM/WAV needs Pro), `pronunciation_dictionary_locators`, and optionally `apply_text_normalization`.
- **Request stitching:** `previous_text` and `next_text` on `convert` give continuity between separate generations. Not available on `eleven_v3`; the docs list v4 as supported.
- **SSML:** v4 does not support SSML or `<break>`. Pauses come from punctuation, and the chime is a separate static file.
- **`audio_effects`** (filter preset, distance, environment reverb, background noise, pan) is available on `convert` at no extra cost. Reverb or pan produce stereo, which is rejected with mono PCM, but MP3 is fine. Phase 8 tries a "station hall" environment and a PA-style filter preset so the demo sounds like a real tannoy. Preset ids come from the audio effects catalog endpoint.
- **Concurrency** (multilingual-class / Flash-class requests): Free 2/4, Starter 3/6, Creator 5/10, Pro 10/20. Excess requests queue (about 50 ms added), they are not rejected. Relay caps in-flight renders with `ELEVENLABS_MAX_CONCURRENCY`.
- **Plans:**
  - Free includes 10K v4 characters or 20K v4 Turbo characters per month, but grants **no commercial rights**, and the **voice library is not available via the API on the free tier**. Default voices work.
  - Starter is $6/month ($1 for the first month). It is the cheapest tier that permits a commercial pitch demo.
- **Zero-retention mode** (`enable_logging=false`) is Enterprise-only. It is relevant to passenger names in production (R11) but not used in the demo.

## 8. Cost model (per full run of A + B + C, live, cold cache)

| Item | Estimate |
|---|---|
| TTS characters: A about 3 versions × 200 (the fixed opening and closing are voiced once), B 9 clips × 140, C 1 × 150. With D17, B's six level 2 and 3 clips cost $0.08/1K, the rest $0.04/1K | ≈ 2,400 chars ≈ **$0.11** |
| LLM: about 5 Haiku 5.5 calls, ≈ 2K input / 0.8K output each at $0.10 / $0.50 per MTok | ≈ **$0.003** |
| LLM: about 2 Sonnet 5.5 calls (retry and staff note) at $2 / $10 per MTok | ≈ **$0.03** |
| **Total** | **≈ $0.16**. A warm-cache rerun costs only the LLM calls. |

Prices live in `providers/pricing.py`. Check them against the provider pages before the pitch.

## 9. Claude API usage notes (for the Anthropic provider)

- **SDK:** `anthropic` (PyPI latest 2.7.0). Use structured outputs via `client.messages.parse(model=..., output_format=PydanticModel, ...)` and read `response.parsed_output`.
- **Do not send `temperature`, `top_p` or `top_k`.** They are rejected on current models.
- **Do not use forced `tool_choice` or assistant prefill.** Both are rejected on current models. Structured outputs replace them.
- **Haiku 5.5 and Sonnet 5.5** run adaptive thinking by default. Set `output_config={"effort": "low"}` for latency.
- **Refusals:** always check `stop_reason` (including `"refusal"`) before reading output. A refusal is logged as `outcome=refused` and follows the normal fallback path. Optional: server-side refusal fallback on Sonnet 5.5 (`betas=["server-side-fallback-2026-07-01"], fallbacks="default"`), behind `ANTHROPIC_SERVER_FALLBACK=true`. Haiku 5.5 has no server-side fallback.
- **Per-call timeout:** `client.with_options(timeout=6.0, max_retries=0)`. The engine owns retries (D14).
- **Prompt caching** is not used in the demo. The prompts are small and the cost is negligible. Revisit if per-site style guides grow.
- **Structured outputs on `claude-haiku-5-5`: not yet verified live** (no key in the Phase 3 build environment). The provider uses `messages.parse`; its request shape is covered by a stub-client test, and `tests/test_live_smoke.py` (`-m live`) verifies it once a key is set. If Haiku rejects it, switch that provider to JSON-in-text validated by the same Pydantic model and record the change here.
- Installed SDK: `anthropic` 2.7.0. Server-side refusal fallback uses `client.beta.messages.parse(..., betas=["server-side-fallback-2026-07-01"], fallbacks="default")`, Sonnet only.
