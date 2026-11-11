"""Upload or update a site's pronunciation dictionary. Used by scripts/sync_pronunciations.py."""

import logging
from pathlib import Path

from relay.pronunciation import (
    PronunciationLock,
    PronunciationRule,
    parse_pls,
    pls_hash,
    read_locks,
    write_lock,
)
from relay.providers.tts_base import DictionarySync, TTSProvider
from relay.sites import SITES_DIR, Site

log = logging.getLogger(__name__)


def dictionary_name(site: Site) -> str:
    return f"relay-{site.id}"


def load_rules(site: Site, sites_dir: Path = SITES_DIR) -> tuple[str, list[PronunciationRule]]:
    """The site's PLS text and the rules in it."""
    if not site.pronunciation_file:
        raise ValueError(f"site {site.id} has no pronunciation_file")
    text = (sites_dir / site.pronunciation_file).read_text(encoding="utf-8")
    return text, parse_pls(text)


def sync_site(tts: TTSProvider, site: Site, sites_dir: Path = SITES_DIR) -> DictionarySync:
    """Create the dictionary the first time, otherwise update it in place (a new version), then
    record the id and version in pronunciation.lock.json so every request uses that version.

    Refuses to run against the mock: a mock id in the lock file would break the live provider.
    """
    if tts.mode != "live":
        raise RuntimeError("TTS is in mock mode (no ELEVENLABS_API_KEY): nothing to sync")
    text, rules = load_rules(site, sites_dir)
    existing = read_locks(sites_dir).get(site.id)
    result = tts.sync_dictionary(
        dictionary_name(site), rules, existing.dictionary_id if existing else None
    )
    write_lock(
        sites_dir,
        site.id,
        PronunciationLock(
            dictionary_id=result.dictionary_id,
            version_id=result.version_id,
            pls_sha256=pls_hash(text),
        ),
    )
    log.info("pronunciation %s: %s (version %s)", site.id, result.action, result.version_id)
    return result
