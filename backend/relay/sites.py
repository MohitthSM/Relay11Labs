"""Site config (sites/*.yaml): zones, languages, house style, repeat policy, gazetteer.

Sites are config, not tables (D12). Loaded once and cached.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from relay.pronunciation import PronunciationLock, read_locks
from relay.schemas import EVENT_TYPES

BACKEND_DIR = Path(__file__).resolve().parents[1]
SITES_DIR = BACKEND_DIR / "sites"
STATIC_DIR = BACKEND_DIR / "static"


class Zone(BaseModel):
    id: str
    name: str
    languages: list[str]  # play order


class Policy(BaseModel):
    """How one event type is announced at this site."""

    zones: list[str]
    interval_s: int  # repeat interval
    max_plays: int | None = None
    duration_s: int | None = None  # the event expires (auto-resolves) after this long
    escalate: bool = False  # play urgency = min(plays_done + 1, 3)
    priority: int = 50
    resolution_plays: int = 0


class Gazetteer(BaseModel):
    """Every proper noun the site knows. Legal in model text only as placeholder values."""

    lines: list[str] = Field(default_factory=list)
    stations: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    airlines: list[str] = Field(default_factory=list)
    aliases: dict[str, list[str]] = Field(default_factory=dict)  # canonical -> other spellings

    def names(self) -> list[str]:
        """Every known name and alias, longest first (so 'Canary Wharf' wins over 'Canary')."""
        base = self.lines + self.stations + self.cities + self.airlines
        every = set(base) | {a for v in self.aliases.values() for a in v}
        return sorted(every, key=len, reverse=True)

    def spellings(self, canonical: str) -> list[str]:
        return [canonical, *self.aliases.get(canonical, [])]


class Segments(BaseModel):
    """Fixed phrases of one language that are voiced once and reused (voice.split_segments).

    A script that starts with an opening, or ends with a closing, has that part cut off and
    cached permanently; only what is left (the dynamic middle) is generated each time.
    """

    openings: list[str] = Field(default_factory=list)
    closings: list[str] = Field(default_factory=list)


class VoiceSettingsConfig(BaseModel):
    """Optional ElevenLabs voice settings. Unset means the voice's own default."""

    stability: float | None = None
    similarity_boost: float | None = None
    style: float | None = None
    speed: float | None = None


class Site(BaseModel):
    id: str
    name: str
    hub_type: str  # underground | airport | bus: picks the system prompt
    voice_id: str = ""  # ONE voice for the whole hub, in every language
    voice_settings: VoiceSettingsConfig = Field(default_factory=VoiceSettingsConfig)
    pronunciation_file: str | None = None  # sites/<name>.pls, synced by sync_pronunciations.py
    pronunciation: PronunciationLock | None = None  # filled from pronunciation.lock.json
    segments: dict[str, Segments] = Field(default_factory=dict)  # by language
    style_guide: str
    allowed_terms: list[str] = Field(default_factory=list)  # capitalised words the LLM may write
    zones: list[Zone]
    policies: dict[str, Policy]
    gazetteer: Gazetteer = Field(default_factory=Gazetteer)

    def zone(self, zone_id: str) -> Zone:
        return next(z for z in self.zones if z.id == zone_id)

    def languages_for(self, event_type: str) -> list[str]:
        """Languages of the zones this event type plays in, in first-seen order."""
        out: list[str] = []
        for zone_id in self.policies[event_type].zones:
            out += [lang for lang in self.zone(zone_id).languages if lang not in out]
        return out


def load_site(path: Path) -> Site:
    site = Site.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if site.pronunciation_file:
        site.pronunciation = read_locks(path.parent).get(site.id)
    zone_ids = {z.id for z in site.zones}
    for event_type, policy in site.policies.items():
        if event_type not in EVENT_TYPES:
            raise ValueError(f"{path.name}: unknown event type {event_type!r}")
        missing = set(policy.zones) - zone_ids
        if missing:
            raise ValueError(f"{path.name}: policy {event_type} uses unknown zones {missing}")
    return site


@lru_cache
def load_sites(directory: str = str(SITES_DIR)) -> dict[str, Site]:
    sites = [load_site(p) for p in sorted(Path(directory).glob("*.yaml"))]
    return {s.id: s for s in sites}


def get_site(site_id: str) -> Site:
    return load_sites()[site_id]
