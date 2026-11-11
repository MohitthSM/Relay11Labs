# Sample clips: characters used

Written by `scripts/render_samples.py` from `manifest.json`. Characters are what
ElevenLabs bills (counted here as one credit each, an upper bound). TTFA = request start
to first audio byte; 0 characters means the clip was already in the cache.

## 2026-11-11T16:40:00+00:00: pytest -m live: test_one_clip_from_elevenlabs (clip in a temp dir, not kept)

| Clip | Model | Script | Chars | TTFA (ms) | Total (ms) |
|---|---|---|---|---|---|
| smoke: 'Customer information.' + 'Holborn and Marylebone are open.' | eleven_v4_turbo | test | 53 | 0 | 0 |

Run total: **53** characters.

## 2026-11-11T16:51:41+00:00: render_samples.py

| Clip | Model | Script | Chars | TTFA (ms) | Total (ms) |
|---|---|---|---|---|---|
| b_en_u1 | eleven_v4_turbo | llm | 124 | 2216 | 2819 |

Run total: **124** characters.

**Total so far: 177 characters.**
