# Sample clips

Real ElevenLabs audio for the pitch, kept small for a free-tier key. `manifest.json` lists every clip ever rendered (cache id, model, voice, characters) and a ledger of the characters spent per run. `report.md` shows the same as tables.

```bash
cd backend && uv run python scripts/render_samples.py         # the plan: characters per clip, no API call
cd backend && uv run python scripts/render_samples.py --go    # render it, then commit this folder
```

The script stops before a run that would pass `--budget` (default 5,000 characters, all runs together).

**Never paid twice.** The script seeds the cache from this folder before planning, so a committed clip costs 0. The app does the same at start-up when TTS is live (`voice.seed_cache`, `RELAY_SAMPLES_DIR`), so the demo replays these clips for free. The clips use the template wording, so they are reused only while the LLM is in mock mode (`RELAY_LLM_MODE=mock`). In scenario B, the clips not in this set (urgency 2, and ES/FR at urgency 3) would still be rendered by a live run (D4).

Files:
- `<clip>.mp3`: what a zone plays, chime included where the app adds one.
- `cache/<id>.mp3`: the segments stitched into them.
