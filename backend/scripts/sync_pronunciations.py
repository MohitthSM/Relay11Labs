"""Upload or update the pronunciation dictionaries for the sites (ElevenLabs).

    uv run python scripts/sync_pronunciations.py              # every site with a .pls file
    uv run python scripts/sync_pronunciations.py tube_station # one site
    uv run python scripts/sync_pronunciations.py --dry-run    # parse and show the rules, no API

First run creates the dictionary. Later runs add or change rules and remove ones you deleted
from the .pls file; that makes a new dictionary version. The id and version are written to
sites/pronunciation.lock.json, and the voice layer then sends them with every request.
Needs ELEVENLABS_API_KEY in .env. Edit sites/<site>.pls, run this, commit the lock file.
"""

import argparse
import sys

from relay.config import get_settings
from relay.pronsync import load_rules, sync_site
from relay.pronunciation import read_locks
from relay.providers import build_tts
from relay.sites import SITES_DIR, load_sites


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "sites", nargs="*", help="site ids (default: all with a pronunciation_file)"
    )
    parser.add_argument("--dry-run", action="store_true", help="parse the .pls, call nothing")
    args = parser.parse_args()

    all_sites = load_sites()
    unknown = [s for s in args.sites if s not in all_sites]
    if unknown:
        print(f"unknown site(s): {', '.join(unknown)}; known: {', '.join(all_sites)}")
        return 2
    sites = [all_sites[s] for s in args.sites] if args.sites else list(all_sites.values())
    sites = [s for s in sites if s.pronunciation_file]
    if not sites:
        print("no site has a pronunciation_file")
        return 0

    tts = None if args.dry_run else build_tts(get_settings())
    if tts is not None and tts.mode != "live":
        print("TTS is in mock mode (no ELEVENLABS_API_KEY, or RELAY_TTS_MODE=mock): use --dry-run")
        return 1

    locks = read_locks(SITES_DIR)
    for site in sites:
        _, rules = load_rules(site)
        known = locks.get(site.id)
        state = f"dictionary {known.dictionary_id}" if known else "not uploaded yet"
        print(f"{site.id}: {len(rules)} rules from {site.pronunciation_file} ({state})")
        if tts is None:
            for r in rules:
                print(f"  {r.grapheme} -> {r.phoneme or r.alias}")
            continue
        result = sync_site(tts, site)
        changed = ", ".join(result.added) or "-"
        removed = ", ".join(result.removed) or "-"
        print(f"  {result.action}: dictionary {result.dictionary_id} version {result.version_id}")
        print(f"  changed: {changed}; removed: {removed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
