"""Pronunciation dictionaries: read the site's PLS file, and remember which version is uploaded.

`sites/<site>.pls` is the source of truth (a standard W3C lexicon file). The upload script turns
it into rules and syncs them to ElevenLabs. The id and version that came back are stored in
`sites/pronunciation.lock.json` (not secret) and read by sites.py, so every TTS request for the
site carries the same dictionary version. No SDK import here: the provider does the API calls.
"""

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

PLS_NS = "{http://www.w3.org/2005/01/pronunciation-lexicon}"
LOCK_NAME = "pronunciation.lock.json"


@dataclass(frozen=True)
class PronunciationRule:
    """One rule: say `grapheme` as `phoneme` (IPA) or as the alias text. Case-sensitive."""

    grapheme: str
    phoneme: str | None = None
    alphabet: str = "ipa"
    alias: str | None = None


class PronunciationLock(BaseModel):
    dictionary_id: str
    version_id: str
    pls_sha256: str  # of the PLS text that was uploaded: tells us when the file has changed


def parse_pls(text: str) -> list[PronunciationRule]:
    """PLS lexicon -> rules. Each lexeme needs a grapheme and either a phoneme or an alias."""
    root = ET.fromstring(text)
    alphabet = root.get("alphabet", "ipa")
    rules: list[PronunciationRule] = []
    for lexeme in root.findall(f"{PLS_NS}lexeme"):
        graphemes = [(g.text or "").strip() for g in lexeme.findall(f"{PLS_NS}grapheme")]
        phoneme = lexeme.find(f"{PLS_NS}phoneme")
        alias = lexeme.find(f"{PLS_NS}alias")
        if not graphemes or not all(graphemes) or (phoneme is None) == (alias is None):
            raise ValueError(f"lexeme {graphemes or '?'} needs a grapheme and a phoneme or alias")
        for grapheme in graphemes:
            if alias is not None:
                rules.append(PronunciationRule(grapheme, alias=(alias.text or "").strip()))
            else:
                assert phoneme is not None
                rules.append(PronunciationRule(grapheme, (phoneme.text or "").strip(), alphabet))
    names = [r.grapheme for r in rules]
    if len(names) != len(set(names)):
        raise ValueError("duplicate grapheme in PLS file")
    return rules


def pls_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_locks(sites_dir: Path) -> dict[str, PronunciationLock]:
    path = sites_dir / LOCK_NAME
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {site_id: PronunciationLock.model_validate(v) for site_id, v in raw.items()}


def write_lock(sites_dir: Path, site_id: str, lock: PronunciationLock) -> None:
    locks = read_locks(sites_dir)
    locks[site_id] = lock
    body = {k: v.model_dump() for k, v in sorted(locks.items())}
    (sites_dir / LOCK_NAME).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
