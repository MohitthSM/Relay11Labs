# Relay: Product Spec

Relay is a context-aware public announcement (PA) system for transport hubs. Live operational events go in, and correct, on-brand, multilingual spoken announcements come out. They play in the right zones and repeat until the event is updated or resolved.

Built as a demo to pitch to ElevenLabs.

## 1. Problem

Routine PA content ("mind the gap", "do not leave luggage unattended") is pre-recorded. Pre-recorded audio cannot take in new information, so anything situational is still read out by staff over a microphone. Examples:

- a Tube line delay with a specific cause
- paging a named late passenger to a specific gate
- a bus moving to a different stand

Manual announcements are inconsistent in wording and quality. They are usually English-only. Staff often forget to repeat them, or never retract them once the situation changes.

## 2. Product in one paragraph

An operational event arrives, either as structured data (a status feed or a form) or as a short staff note. An LLM writes the announcement script in the operator's house style. A deterministic **fact lock** checks the script. If the script contains any fact that is not in the event, it is rejected and Relay uses a deterministic template instead. ElevenLabs voices the approved script in each configured language. A scheduler plays and repeats the audio in the target zones. When the event is updated, the old audio is superseded. When the event is resolved, the old audio stops and an optional "resolved" message plays.

## 3. Users

### 3.1 Station operator (Tube station / bus station control room)
- **Context:** one person watches many screens and handles disruption across a station. They cannot spend time drafting wording.
- **Needs:**
  - Status changes (from a feed or entered by hand) become announcements with no typing.
  - They can see exactly what is playing, where, and when it plays next.
  - They can update or resolve an event in one click and know that the old message stops.
- **Success:** within seconds of a status change, the right message is playing on the right platforms. Nothing stale is left playing.

### 3.2 Airport gate agent
- **Context:** busy at the gate desk, with a boarding deadline. Often needs to page a specific passenger.
- **Needs:**
  - Type a short note ("final call Mr John Smith BA117 NYC gate 23") or fill a three-field form, then confirm.
  - The page goes out in English, Spanish and French.
  - Urgency escalates across repeats with no further input.
  - "Passenger boarded" stops the page immediately.
- **Success:** the page starts in under 10 seconds and is never read with a wrong gate or flight number.

### 3.3 Secondary: demo audience (ElevenLabs)
They need to see three things: why voice quality and multilingual TTS matter here, why the fact lock makes LLM-written announcements safe, and what it costs per announcement.

## 4. Core flows

### F1. Structured event → announcement (no human writing)
1. The event arrives through `POST /events`, the console form, the TfL poller or a scenario replay.
2. Relay validates the payload against the typed schema for its event type.
3. The script engine writes scripts for each language and urgency level. Each script passes the fact lock or is replaced by a template.
4. The voice layer renders the audio. Audio is cached by content hash, so a repeat never costs a second TTS call.
5. The scheduler creates playback jobs for the target zones, using the repeat policy for that event type.
6. Zone players in the browser play the chime, then each language in order.

### F2. Staff note → announcement (human confirms)
1. The agent types a free-text note into the console.
2. The LLM (escalation model) extracts a typed event. Every extracted value must appear in the note text, or the field is left empty and flagged.
3. The console shows the extracted fields and the generated English script. The agent edits fields if needed and clicks **Broadcast**.
4. Flow continues from F1 step 3. Nothing from a staff note plays without a human click.

### F3. Update
1. A new version of an existing event arrives, keyed by `event_key` (for example, Jubilee goes from severe to minor delays).
2. Relay generates new scripts and audio for the new version.
3. Pending playback jobs for the old version are cancelled. A clip that is already playing finishes, because Relay never cuts audio mid-sentence.
4. New jobs are scheduled.

### F4. Resolve
1. The operator clicks **Resolve**, or the feed reports a return to normal (for example, Jubilee shows Good Service).
2. All jobs for the event are cancelled.
3. If the event type has a resolution message, it is generated (same pipeline, same fact lock) and played N times. For scenario A, that is "good service has resumed", played twice.

### F5. Monitor
- The **console** shows active events, each announcement's script per language, its source (LLM or template), any fact-lock rejection reasons, and its audio.
- The **zones** view shows each zone and what it is playing now and next.
- The **metrics** view shows, per announcement and in aggregate:
  - which model handled it, plus tokens, cost and latency
  - fact-lock rejection rate and template fallback rate
  - TTS characters and cost, and audio cache hit rate
  - time from event received to first play

## 5. Demo scenarios

Every scenario can run three ways:
- **mock mode**: no API keys at all
- **live mode**: Claude and ElevenLabs
- **time-compressed**: `RELAY_TIME_SCALE`, so a 20-minute repeat cycle runs in about 20 seconds on stage

Each scenario ships as a replay file in `backend/scenarios/` that posts timed events.

### A. Tube: Jubilee line disruption (station operator)
| t | Event | Expected announcement (EN, illustrative) |
|---|---|---|
| 0 | `line_status` Jubilee, `severe_delays`, reason `signal_failure`, location "Canary Wharf" | "Customer information. There are severe delays on the Jubilee line due to a signal failure at Canary Wharf. Please consider alternative routes. We apologise for the disruption." |
| +6 min | Same `event_key`, status → `minor_delays` | Updated: "There are minor delays on the Jubilee line…". The severe-delays message stops. |
| +12 min | Status → `good_service` (resolve) | "Good service has now resumed on the Jubilee line." Plays 2×, then silence. |

- **Zones:** Jubilee eastbound platform, Jubilee westbound platform, ticket hall.
- **Repeat:** every 5 min while active.
- **Language:** English only.
- **Phase 7:** the same flow is driven by the live TfL Unified API. The replay file stays as the stage version, because the real line will usually show good service.

**What it proves:** updates replace old content automatically, and the cause is stated correctly and never made up.

### B. Airport: final-call passenger page (gate agent)
- **Event:** `passenger_page`, passenger "John Smith", flight `BA117`, destination "New York", gate `23`, page type `final_call`.
- **Languages:** English, then Spanish, then French. This is one sequence, played back to back.
- **Urgency:** escalates over 3 repeats, 2 min apart.
  1. Level 1, polite final call: "This is a final call for passenger John Smith, travelling to New York on flight BA117. Please go to Gate 23 immediately."
  2. Level 2, firmer: "…Passenger John Smith, your flight is ready to depart. Please make your way to Gate 23 now."
  3. Level 3, last call: "…This is the last call for passenger John Smith on flight BA117 to New York. Gate 23 is about to close."

  All 9 clips (3 levels × 3 languages) are rendered when the event is received. Playback never waits on generation.
- **Zones:** Gate 23, Concourse B.
- **Stop:** after 3 plays, or when the agent clicks **Passenger boarded** (resolve). There is no resolution message.
- **Fact-lock moment:** with the mock "hallucinate" toggle on, the mock LLM writes "Gate 32" or invents "your bags will be offloaded". The console shows the rejection reasons, and the template version plays instead.

**What it proves:** one voice across EN/ES/FR (Eleven v4 keeps one voice fluent across languages), urgency escalation, and the fact lock.

### C. Bus station: stand change (station operator)
- **Event:** `stand_change`, route `25`, from stand `B`, to stand `F`. Reason optional.
- **Announcement:** "Route 25 is now departing from Stand F, not Stand B. Please go to Stand F."
- **Repeat:** every 3 min for 20 min. That is 7 plays, at t = 0, 3, 6, 9, 12, 15 and 18 min, then the event auto-resolves.
- **Zones:** bus station concourse, Stand B, Stand F.
- **Language:** English.

**What it proves:** "repeat for a period, then stop" with no staff effort, and the audio cache. The clip is generated once and played 7 times, so TTS is billed once.

## 6. Functional requirements

| ID | Requirement |
|---|---|
| R1 | Event types: `line_status`, `passenger_page`, `stand_change`, `staff_note` (staff notes are converted into one of the other three). |
| R2 | Each event has a stable `event_key` and an incrementing `version`. Each update creates a new version. |
| R3 | **Fact lock:** a generated script may contain only facts present in the event, meaning times, platforms, gates, stands, names, flight numbers, routes, lines, stations, causes and statuses. On any violation, Relay rejects the script and uses the deterministic template for that language. |
| R4 | Every LLM call and every TTS call has a mock implementation that works with no API keys. Missing keys mean mock mode is used automatically, so the demo never breaks. |
| R5 | Per-language templates exist for every event type, urgency level and language. Templates are the fallback and the reference wording. |
| R6 | Model routing: the default model writes scripts. The escalation model handles (a) the retry after a fact-lock failure and (b) all staff-note events. Both are configurable in `.env`. |
| R7 | Every LLM call is logged with model, purpose, attempt, tokens, cost, latency and outcome. Every TTS render is logged with provider, model, characters, cost and cache hit. |
| R8 | A zone plays one clip at a time. If several announcements are due, higher priority plays first. Clips are never cut mid-play. |
| R9 | Updating or resolving an event cancels its pending plays within one scheduler tick (1 s). |
| R10 | Live LLM calls have a timeout of 6 s. A timeout counts as a failure, and Relay falls back to the template so the announcement is not delayed. |
| R11 | Passenger names are personal data. They are shown in the console and spoken, but are not written to the metrics view or logs. Audio and scripts for resolved pages are purged after `RELAY_PII_RETENTION_HOURS` (default 24). |

## 7. Non-goals

- **Emergency, evacuation or security announcements.** These are safety-critical and must stay pre-approved recordings. Relay refuses any event type outside R1.
- **Integration with real PA hardware** (Dante, SIP paging, amplifiers, zone controllers). Browser panels stand in for zones.
- **Integration with real airline or airport systems** (DCS, FIDS, AODB). Airport and bus events are simulated. Only TfL line status is live (Phase 7).
- Authentication, user roles, multi-tenancy, audit-grade logging.
- Free-form general announcements with no event type.
- Voice cloning of staff, speech-to-text input, or a mobile app.
- Production deployment, high availability, or horizontal scaling. Relay is a single process with SQLite.
- Accessibility displays (dot-matrix and screen captions). The console shows the text, but driving signage is out of scope.
- Languages beyond EN/ES/FR in the demo. The design is language-agnostic. Adding a language means adding its templates and claim lexicon.

## 8. Success criteria for the pitch

1. All three scenarios run end to end in mock mode with no keys and no network.
2. In live mode, an event plays its first audio in under 5 s for one language, and under 8 s for scenario B's 9 clips.
3. A fact-lock rejection can be shown on demand, and the console explains why the script was rejected.
4. The metrics page shows per-announcement model routing and cost. A full run of all three scenarios costs less than $0.50 (see ARCHITECTURE.md §8).
