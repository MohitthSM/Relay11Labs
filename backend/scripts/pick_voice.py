"""Shortlist ElevenLabs voices for the PA (one voice per site, in every language it speaks).

    uv run python scripts/pick_voice.py                  # rank for EN, ES and FR
    uv run python scripts/pick_voice.py --languages en   # rank for one language
    uv run python scripts/pick_voice.py --top 20

Lists the voices this account can use (GET /v2/voices; costs no characters) and ranks them:
verified in every site language, an announcer or narration use case, not legacy (the old default
voices end on 2026-12-31). Listen to the top few in EN, ES and FR, then put the chosen id in
`voice_id:` of sites/*.yaml. Needs ELEVENLABS_API_KEY in .env; in mock mode it shows two
made-up voices so the script can be checked without a key.
"""

import argparse
import sys

from relay.config import get_settings
from relay.providers import build_tts
from relay.voicepick import pa_score, shortlist


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--languages", default="en,es,fr", help="comma-separated ISO 639-1")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    languages = [x.strip() for x in args.languages.split(",") if x.strip()]

    tts = build_tts(get_settings())
    if tts.mode != "live":
        print("TTS is in mock mode (no ELEVENLABS_API_KEY): showing the mock's voices")
    voices = tts.list_voices()
    print(f"{len(voices)} voices; ranked for {', '.join(languages)}\n")
    print(f"{'score':>5}  {'voice_id':<22} {'name':<24} {'category':<12} languages  labels")
    for v in shortlist(voices, languages, args.top):
        labels = ", ".join(f"{k}={val}" for k, val in sorted(v.labels.items()))
        legacy = " LEGACY" if v.legacy else ""
        print(
            f"{pa_score(v, languages):>5}  {v.voice_id:<22} {v.name[:24]:<24} "
            f"{v.category[:12]:<12} {','.join(v.languages) or '-':<10} {labels}{legacy}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
