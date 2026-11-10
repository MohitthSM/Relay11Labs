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
                │                     api/playback (SSE) ◄── bus ◄───┘               │
                └───────────────────────────────┬───────────────────────────────────┘
                                                │ SSE "play" commands + /audio/*.mp3
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
| Events service | `relay/events.py` | Creates, updates and resolves events. Bumps `version`. Calls the script engine and the voice layer, then hands off to the scheduler. |
| Script engine | `relay/script/engine.py` | For each language × urgency level: calls the LLM, runs the fact lock, retries once on the escalation model, then falls back to the template. Produces the `Announcement` rows. |
| Fact lock | `relay/script/factlock.py` | A pure function `check(script, event, site, lang) -> list[Violation]`. No I/O. Heavily unit-tested. |
| Templates | `relay/script/templates.py` | Deterministic wording per (event type, urgency, language). Uses `str.format` with the same placeholders as the LLM output. |
| Prompt | `relay/script/prompt.py` | Builds the system prompt (house style + rules) and the user message (the event facts as placeholders). |
| Normaliser | `relay/script/normalize.py` | Turns an approved script into spoken text. Spaces out codes (`BA117` → `B A 1 1 7`) and applies per-language time phrasing. Runs **after** the fact lock. |
| LLM providers | `relay/providers/llm_*.py` | A `LLMProvider` Protocol with an `AnthropicProvider` and a `MockLLMProvider`. |
| TTS providers | `relay/providers/tts_*.py` | A `TTSProvider` Protocol with an `ElevenLabsProvider` and a `MockTTSProvider`. |
| Pricing | `relay/providers/pricing.py` | Price table: USD per MTok per Claude model and USD per 1K characters per ElevenLabs model. Used only for logging and metrics. |
| Voice layer | `relay/voice.py` | Hashes the render request, returns the cached clip if present, otherwise calls the TTS provider. Writes the `AudioClip` row and file, and reads the clip duration. |
| Scheduler | `relay/scheduler.py` | Every tick: finds due `PlaybackJob`s, checks whether each zone is busy, emits play commands, advances repeat counters, marks jobs done. |
| Bus | `relay/bus.py` | In-memory fan-out of play commands to connected SSE clients. Each zone subscriber gets an `asyncio.Queue`. |
| Sources | `relay/sources/tfl.py`, `relay/sources/replay.py` | The TfL line-status poller (Phase 7) and the scenario replayer. Both just call the events service. |
| API | `relay/api/*.py` | FastAPI routers: `events`, `playback` (SSE + audio files), `metrics`, `scenarios`. |
| Frontend | `frontend/src/` | Console (event list, form, staff note, announcement inspector), Zones (one panel per zone with an `<audio>` queue), Metrics. |

### Provider interfaces (the only seams for external services)

```python
class LLMProvider(Protocol):
    def generate_scripts(self, req: ScriptRequest, model: str) -> LLMResult: ...
    def extract_event(self, note: str, site: Site, model: str) -> LLMResult: ...

class TTSProvider(Protocol):
    def synthesize(self, req: RenderRequest) -> TTSResult: ...   # bytes + metadata
```

`LLMResult` carries the parsed output plus `model`, `input_tokens`, `output_tokens`, `latency_ms` and `stop_reason`. It is **all** that the engine sees, so the engine cannot tell whether it is talking to the mock or to the real provider.

- **`MockLLMProvider`.** Fills the reference template with light deterministic phrasing variation, so the fact lock is exercised realistically. Reports fake token counts. With `RELAY_MOCK_HALLUCINATE=true` it injects one invented fact per event (a swapped gate number, an invented cause, or "baggage will be offloaded") for the on-stage fact-lock demo.
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
  priority: int              # from site policy; pages > disruption > info
  created_at, resolved_at

LLMCall                      # one row per API attempt; feeds the metrics page
  id, event_id FK, purpose (script | extract), attempt (1 | 2)
  model, input_tokens, output_tokens, cost_usd, latency_ms
  outcome: accepted | rejected | timeout | error | refused
  violations: JSON           # fact-lock reasons when rejected

Announcement                 # one per (event version, language, urgency level)
  id, event_id FK, language, urgency (1..3), is_resolution: bool
  script: str                # with placeholders filled, as checked by the fact lock
  spoken_text: str           # after normalize.py; what is sent to TTS
  source: llm | template
  llm_call_id FK | None      # which call produced the accepted script
  clip_id FK -> AudioClip

AudioClip                    # content-addressed cache entry
  id: str PK                 # sha256(provider, model_id, voice_id, language, settings, spoken_text)
  path, provider (elevenlabs | mock), model_id, voice_id, language
  characters, cost_usd, duration_s, created_at, hits: int

PlaybackJob                  # "play this event's sequence in this zone on this cadence"
  id, event_id FK, zone_id
  next_at: datetime, interval_s: int, plays_done: int, max_plays: int | None, ends_at | None
  escalate: bool             # if true, play urgency = min(plays_done + 1, 3)
  priority, state: scheduled | done | cancelled

PlayLog
  id, job_id FK, zone_id, announcement_ids: JSON, played_at
```

Why one `Event` row per version rather than an event table plus a history table: it is one table and one rule ("the latest active row with this key is current"), and old versions stay inspectable in the console.

## 4. The fact lock

**Rule:** a generated script may contain only facts that are present in the event. If any check fails, the script for that language is rejected. Relay retries once on the escalation model with the violations as feedback. If that also fails, it uses the template.

The LLM never writes facts as text. It writes **placeholders**, and code fills them from the event payload:

```
LLM output (fr):  "Dernier appel pour {passenger_name}, passager du vol {flight_number} à destination de {destination}. Veuillez vous présenter immédiatement à la porte {gate}."
```

`check()` runs five layers. Each layer is a small, separately tested function.

| # | Layer | Rejects when |
|---|---|---|
| 1 | Placeholders | A `{name}` is not a field of this event type, or a field marked `required` for this type/urgency is missing (for example, a final call without `{gate}`). |
| 2 | Digits and codes | The *unfilled* text contains any digit, a time (`\d{1,2}[:.h]\d{2}`), or an alphanumeric code (`[A-Z]{1,3}\d+`). Facts can only arrive through placeholders. |
| 3 | Number words | The unfilled text contains a cardinal number word from the per-language list (`one…ninety`, `uno…`, `un…`). A small allow-list covers idioms such as "no one". |
| 4 | Proper nouns | A capitalised word that is not at the start of a sentence is not in the site's brand allow-list. In addition, at any position, a word that appears in the site **gazetteer** (all stations, lines, gates, cities and airlines the site knows) is rejected, because it is only legal as a placeholder value. This catches "Victoria line" in a Jubilee event. |
| 5 | Claim lexicon | The text asserts an operational claim the event does not support. A per-language YAML maps claim keys to regexes (`cancelled`, `delayed`, `closing`, `offloaded`, `suspended`, `diverted`, `minutes`, and every disruption **reason** such as `signal_failure` or `customer_incident`). The event payload determines which claim keys are allowed: `severe_delays` + `signal_failure` allows exactly those. |

Also enforced: maximum word count per language, no `[` `]` `<` `>` `/` (so the LLM cannot inject audio tags, SSML or IPA; those are added only by code), and the output language matches the requested one (cheap check: at least one template stop-word for that language is present).

**Known limits, stated plainly:**
- A capitalised word at the start of a sentence passes layer 4 unless it is in the gazetteer.
- Layer 5 is only as complete as its lexicon.

Templates are the safety net. The lexicon grows whenever a rejection is missed, and every miss becomes a test case.

**Staff notes.** Extraction has its own lock. Every extracted string field must appear in the note (case-insensitive, whitespace-normalised). Enum fields such as `reason` must map from a word in the note via the claim lexicon. Anything else is dropped and shown to the agent as "not found in note". The agent confirms before broadcast.

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
6. **Normalise.** `normalize.py` produces the spoken text. `BA117` becomes `B A 1 1 7`, and the format is consistent across languages. 9 `Announcement` rows are written.
7. **Render the voice.**
   - For each announcement, `voice.render()` hashes (provider, model, voice, language, settings, text). On a cache hit it increments `hits` and returns. On a miss it calls `ElevenLabsProvider.synthesize()`:
     ```python
     client.text_to_speech.convert(
         voice_id=site.voice_id, model_id="eleven_v4_turbo", text=spoken_text,
         language_code="fr", output_format="mp3_44100_128", seed=stable_seed,
         pronunciation_dictionary_locators=[site_dictionary_locator],
     )
     ```
   - Renders run in a thread pool, capped by a semaphore at the plan's concurrency limit (`ELEVENLABS_MAX_CONCURRENCY`, default 2).
   - The bytes go to `data/audio/<hash>.mp3`, the duration is read with `mutagen`, and an `AudioClip` row is written.
8. **Schedule.**
   - The site policy for `passenger_page` is: zones `[gate-23, concourse-b]`, interval 120 s, `max_plays=3`, `escalate=true`.
   - Two `PlaybackJob`s are inserted with `next_at=now`.
9. **Tick.**
   - Within 1 s, the scheduler sees `gate-23` due and idle. Its `busy_until` is in memory, computed from clip durations.
   - It picks urgency level `plays_done + 1 = 1` and builds the sequence `[chime, en/1, es/1, fr/1]`.
   - It publishes `{zone: "gate-23", event_id, items: [{url: "/audio/<hash>.mp3", caption, language}...]}` to the bus.
   - It sets `busy_until`, increments `plays_done`, sets `next_at += 120 s / RELAY_TIME_SCALE`, and writes a `PlayLog`.
10. **Play.**
    - The Zones page holds one `EventSource('/playback/stream?zone=gate-23')` per zone panel. It receives the command, adds the items to that panel's `<audio>` queue and plays them in order.
11. **Escalate and stop.**
    - Plays 2 and 3 use urgency levels 2 and 3. After play 3, `plays_done == max_plays` and the job becomes `done`.
    - If the agent clicks **Passenger boarded** first, `events.resolve()` marks the event resolved and cancels its jobs. Nothing new plays, and a clip that is already playing finishes.

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
| D7 | **`eleven_v4_turbo` as the default TTS model.** See §7. | $0.04 per 1K characters (Flash pricing), 90+ languages, about 100 ms model latency, IPA phoneme dictionaries in non-English languages, and one voice that is fluent across languages. | No SSML or `<break>` support on v4, so pauses come from punctuation and the chime is a separate file. `eleven_v4` ($0.08) can be switched in by config for higher quality. |
| D8 | **SSE for playback commands, not WebSockets.** | One-way server → browser fits SSE exactly. Native `EventSource` reconnects on its own, and FastAPI returns a `StreamingResponse` with no extra library. | No browser → server channel on the same socket. Acknowledgements use plain `POST`s. |
| D9 | **The server owns the zone clock.** `busy_until` is computed from clip durations. | One authority for ordering and priority. The browser stays a dumb player. | If a browser tab lags, server and audio drift slightly. This is acceptable for a demo, and real PA hardware would report completion. |
| D10 | **asyncio scheduler in-process with a 1 s tick, not APScheduler or Celery.** | About 60 lines that can be explained in a review. Jobs live in SQLite, so the next tick after a restart resumes them. | Single process only, which is a non-goal anyway. |
| D11 | **`RELAY_TIME_SCALE`** divides every interval. | A 20-minute scenario C runs in 20 s on stage, and tests run instantly. | Every interval must go through one helper (`scaled(seconds)`), which CLAUDE.md enforces. |
| D12 | **Sites as YAML, not DB tables.** | Zones, policies and house style are config that changes rarely and belongs in version control. | Changing them needs a restart. |
| D13 | **Mock mode chosen automatically when a key is missing.** It can also be forced per provider. | "The demo never breaks." A dead key on stage degrades to mock and does not crash. | Live failures could be hidden, so the console header always shows `LLM: live/mock · TTS: live/mock`. |
| D14 | **No retries inside providers beyond the SDK defaults.** The engine owns fallback. | One place decides what happens on failure. | None. |

## 7. ElevenLabs: what the current docs say (checked 2026-11-10)

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
  - Created from a PLS file with `client.pronunciation_dictionaries.create_from_file(file=..., name=...)`.
  - Passed to a request as `pronunciation_dictionary_locators=[PronunciationDictionaryVersionLocator(pronunciation_dictionary_id, version_id)]`, with at most 3 per request.
  - Phoneme rules (IPA/CMU) work only on `eleven_v4`, `eleven_v4_turbo`, `eleven_flash_v2` and `eleven_v3`. Other models need alias rules.
  - Non-English IPA needs v4.
  - Matching is case-sensitive.
  - Relay keeps one PLS per site (station names such as Marylebone, Southwark, Holborn) and uploads it once with `scripts/sync_pronunciations.py`.
- **`convert` parameters Relay uses:** `language_code` (ISO 639-1), `seed` (best-effort determinism), `output_format` (default `mp3_44100_128`; 192 kbps needs Creator, 44.1 kHz PCM/WAV needs Pro), `pronunciation_dictionary_locators`, and optionally `apply_text_normalization`.
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
| TTS characters: A about 3 versions × 200, B 9 clips × 200, C 1 × 150 | ≈ 2,600 chars × $0.04/1K ≈ **$0.10** |
| LLM: about 5 Haiku 5.5 calls, ≈ 2K input / 0.8K output each at $0.10 / $0.50 per MTok | ≈ **$0.003** |
| LLM: about 2 Sonnet 5.5 calls (retry and staff note) at $2 / $10 per MTok | ≈ **$0.03** |
| **Total** | **≈ $0.15**. A warm-cache rerun costs only the LLM calls. |

Prices live in `providers/pricing.py`. Check them against the provider pages before the pitch.

## 9. Claude API usage notes (for the Anthropic provider)

- **SDK:** `anthropic` (PyPI latest 2.7.0). Use structured outputs via `client.messages.parse(model=..., output_format=PydanticModel, ...)` and read `response.parsed_output`.
- **Do not send `temperature`, `top_p` or `top_k`.** They are rejected on current models.
- **Do not use forced `tool_choice` or assistant prefill.** Both are rejected on current models. Structured outputs replace them.
- **Haiku 5.5 and Sonnet 5.5** run adaptive thinking by default. Set `output_config={"effort": "low"}` for latency.
- **Refusals:** always check `stop_reason` (including `"refusal"`) before reading output. A refusal is logged as `outcome=refused` and follows the normal fallback path. Optional: server-side refusal fallback on Sonnet 5.5 (`betas=["server-side-fallback-2026-07-01"], fallbacks="default"`), behind `ANTHROPIC_SERVER_FALLBACK=true`. Haiku 5.5 has no server-side fallback.
- **Per-call timeout:** `client.with_options(timeout=6.0, max_retries=0)`. The engine owns retries (D14).
- **Prompt caching** is not used in the demo. The prompts are small and the cost is negligible. Revisit if per-site style guides grow.
- **Verify in Phase 3** that structured outputs are supported on `claude-haiku-5-5`. If they are not, request JSON in text and validate it with the same Pydantic model.
