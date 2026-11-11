# Sample clips: characters used

Written by `scripts/render_samples.py` from `manifest.json`. Characters are what
ElevenLabs bills (counted here as one credit each, an upper bound). TTFA = request start
to first audio byte; 0 characters means the clip was already in the cache.

## 2026-11-11T16:40:00+00:00: pytest -m live: test_one_clip_from_elevenlabs (clip in a temp dir, not kept)

| Clip | Model | Script | Chars | TTFA (ms) | Total (ms) |
|---|---|---|---|---|---|
| smoke: 'Customer information.' + 'Holborn and Marylebone are open.' | eleven_v4_turbo | test | 53 | n/a | n/a |

Run total: **53** characters.

## 2026-11-11T16:51:41+00:00: render_samples.py

| Clip | Model | Script | Chars | TTFA (ms) | Total (ms) |
|---|---|---|---|---|---|
| b_en_u1 | eleven_v4_turbo | template (mock LLM) | 124 | 2216 | 2819 |

Run total: **124** characters.

## 2026-11-11T16:58:27+00:00: render_samples.py

| Clip | Model | Script | Chars | TTFA (ms) | Total (ms) |
|---|---|---|---|---|---|
| b_en_u1 | eleven_v4_turbo | template (mock LLM) | 0 | 1 | 1 |
| b_en_u3 | eleven_v4 | template (mock LLM) | 151 | 3711 | 4210 |
| b_es_u1 | eleven_v4_turbo | template (mock LLM) | 128 | 2061 | 2111 |
| b_fr_u1 | eleven_v4_turbo | template (mock LLM) | 131 | 1852 | 1894 |
| b_en_u2 | eleven_v4 | template (mock LLM) | 134 | 3262 | 3542 |
| b_es_u2 | eleven_v4 | template (mock LLM) | 131 | 3297 | 3339 |
| b_fr_u2 | eleven_v4 | template (mock LLM) | 136 | 2935 | 2976 |
| b_es_u3 | eleven_v4 | template (mock LLM) | 157 | 3292 | 3351 |
| b_fr_u3 | eleven_v4 | template (mock LLM) | 157 | 3251 | 3312 |
| compare_b_en_u1_eleven_v4_turbo | eleven_v4_turbo | template (mock LLM) | 0 | 1 | 1 |
| compare_b_en_u1_eleven_v4 | eleven_v4 | template (mock LLM) | 124 | 2446 | 2490 |
| a_v1_en | eleven_v4_turbo | template (mock LLM) | 173 | 553 | 2039 |

Run total: **1422** characters.

**Total so far: 1599 characters.**
