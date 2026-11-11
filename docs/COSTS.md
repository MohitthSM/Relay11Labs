# Costs: a busy day at one station

**Short version:** a bad weekday at Canary Wharf, with 204 announcement plays across three zones, bills **802 TTS characters, about $0.03**. Voicing every play fresh would bill 33,102 characters ($1.32), so the cache saves 97.6% of characters. A month of days like this (about 24K characters) fits in the ElevenLabs **Starter** plan's 75K characters. Without the cache it would need **Pro**.

## Prices used (checked 2026-11-11)

ElevenLabs API, per 1K characters, the same on every plan ([elevenlabs.io/pricing/api](https://elevenlabs.io/pricing/api)):

| Model | Relay uses it for | USD / 1K chars |
|---|---|---|
| `eleven_v4_turbo` (v4 Turbo) | `live` route: first plays, updates, resolution messages (D17) | 0.04 |
| `eleven_v4` (v4) | `scheduled` route: escalation levels 2 and 3 | 0.08 |

Plans: Starter $6/month with 75K characters, Creator $22/month with 275K, Pro $99/month with 1.24M. Starter is the cheapest plan with commercial rights (ARCHITECTURE §7).

Claude, per million tokens, input / output ([platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing)): `claude-haiku-5-5` $0.10 / $0.50 (scripts), `claude-sonnet-5-5` $2 / $10 (retries, staff notes). These are the values in `backend/relay/providers/pricing.py`.

## The day

Canary Wharf, Jubilee line, service 05:30 to 00:30. A line-status announcement plays in three zones (eastbound platform, westbound platform, ticket hall) every 5 minutes while the line is disrupted. Each "good service" resolution plays twice per zone. The times are invented, but each change is the kind of status TfL reports:

| Time | Status (TfL poll) | What Relay does |
|---|---|---|
| 07:40 | minor delays, train fault | create |
| 07:41 | the same again | duplicate (D21): nothing |
| 08:10 | severe delays, train fault | update; held for an operator (D20), approved at once |
| 08:50 | minor delays, train fault | update |
| 09:20 | good service | resolve; "good service" plays 2x |
| 12:15 | minor delays, customer incident at Canada Water | create |
| 12:45 | good service | resolve |
| 17:20 | severe delays, signal failure at London Bridge | create, approved |
| 17:50 | part suspended, same cause | update, approved |
| 18:40 | severe delays, same cause | update, approved |
| 19:10 | minor delays, same cause | update |
| 19:40 | good service | resolve |
| 22:00 | minor delays, train fault (the morning's words again) | create |
| 22:30 | good service | resolve |

## Method

`backend/scripts/simulate_day.py` runs this day through the real events service, script engine, voice layer and scheduler in mock mode, on a fake clock, against a throwaway database. Every figure below comes from the `VoiceRender` and `PlayLog` rows that the metrics page reads.

Mock TTS bills `len(text)` characters, which is how ElevenLabs bills, so the character counts are exact for the template wording. An LLM-written script is usually within a few characters of it. Run it yourself:

```bash
cd backend && uv run python scripts/simulate_day.py
```

## Results

| Measure | Value |
|---|---|
| Event versions | 9 (plus 4 resolution messages: 13 announcements voiced) |
| Characters per announcement | 49 to 196, mean 137 (a line-status message with opening and closing is 170 to 196; "good service has resumed" is 49) |
| Plays (zone × repeat) | 204 |
| Announcements served whole from the cache | 4 of 13 (the repeated morning wording and three identical resolutions) |
| Segments served from the cache | 19 of 31 (the fixed "Customer information." opening and the apology closing are voiced once, D18) |
| **Character cache hit rate across all plays** | **97.6%** |

| Strategy | Characters / day | TTS cost / day | × 30 days |
|---|---|---|---|
| No cache: TTS on every play | 33,102 | $1.32 | ~993K chars, $39.70 → needs **Pro** |
| One render per announcement, no shared segments | 1,781 | $0.071 | ~53K chars, $2.14 |
| **Relay: content-hash cache + fixed segments** | **802** | **$0.032** | **~24K chars, $0.96 → fits Starter** |

Where the saving comes from:
- **Repeats are free (D5).** 204 plays come from 13 renders. That is 95% of the saving.
- **Fixed phrases are voiced once (D18).** The opening and the closing make up more than half of a disruption message. Sharing them halves what is left: 1,781 → 802 characters.
- **Identical events cost nothing (D21).** The 07:41 repeat poll made no announcement at all.

**LLM cost** for the same day is 13 script calls on Haiku 5.5, about 2K input and 0.3K output tokens each: about **$0.005**. A fact-lock retry on Sonnet 5.5 adds about $0.007 each. Scripts are not cached, because each version has new facts. The token counts are an estimate: the mock LLM does not report real ones.

**All in,** a bad day at one station costs about **4 cents**.

## What this does not cover

- **Scenario B (airport pages)** is more expensive per event. Nine clips per page (3 languages × 3 levels), and levels 2 and 3 go to `eleven_v4` at twice the price. One page is about 1,250 characters, about $0.085 (ARCHITECTURE §8). A gate area paging 40 passengers a day would bill about 50K characters ($3.40): the cache helps less there, because every page has a different name.
- **Real rendering.** The character counts above are confirmed by real renders. The scenario B clips billed 124 to 157 characters each, exactly the planned counts (`docs/samples/report.md`). Time to first audio was 1.9 to 2.2 s on v4 Turbo and 2.5 to 3.7 s on v4, from a cloud container through a proxy. Committed clips replay from the cache at zero cost.
- **`audio_effects`**, which has no extra cost per the docs, and the Enterprise zero-retention option.
