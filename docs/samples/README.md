# Sample clips

Not generated yet: the build environment had no ElevenLabs key (PLAN.md Phase 8, "Live verification").

With `ELEVENLABS_API_KEY` (and optionally `ANTHROPIC_API_KEY`) in `.env`:

```bash
cd backend && uv run python scripts/render_samples.py
```

This writes the following here:
- `b_<lang>_u<level>.mp3`: scenario B, the 9 clips.
- `a_v1_severe.mp3`, `a_v2_minor.mp3`, `a_resolved.mp3`: scenario A.
- `compare_b_<lang>_u1_<model>.mp3`: the v4 Turbo vs v4 comparison.
- `report.md`: model, characters, cost, time to first audio and total time per clip.

Commit them with the report.
