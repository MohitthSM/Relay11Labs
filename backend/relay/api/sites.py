"""GET /sites: what the console needs to build its forms and zone map. Config only, no secrets
(voice ids and pronunciation locators stay on the server)."""

from fastapi import APIRouter
from pydantic import BaseModel

from relay.sites import Site, load_sites


class ZoneOut(BaseModel):
    id: str
    name: str
    languages: list[str]


class PolicyOut(BaseModel):
    event_type: str
    zones: list[str]
    interval_s: int
    max_plays: int | None
    duration_s: int | None
    escalate: bool
    priority: str
    approval: str


class SiteOut(BaseModel):
    id: str
    name: str
    hub_type: str
    languages: list[str]
    zones: list[ZoneOut]
    policies: list[PolicyOut]
    lines: list[str]  # gazetteer: suggestions for the forms
    stations: list[str]
    cities: list[str]


def site_out(site: Site) -> SiteOut:
    languages: list[str] = []
    for zone in site.zones:
        languages += [lang for lang in zone.languages if lang not in languages]
    return SiteOut(
        id=site.id,
        name=site.name,
        hub_type=site.hub_type,
        languages=languages,
        zones=[ZoneOut(id=z.id, name=z.name, languages=z.languages) for z in site.zones],
        policies=[
            PolicyOut(
                event_type=event_type,
                zones=p.zones,
                interval_s=p.interval_s,
                max_plays=p.max_plays,
                duration_s=p.duration_s,
                escalate=p.escalate,
                priority=p.priority,
                approval=p.approval,
            )
            for event_type, p in site.policies.items()
        ],
        lines=site.gazetteer.lines,
        stations=site.gazetteer.stations,
        cities=site.gazetteer.cities,
    )


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/sites")
    def sites() -> list[SiteOut]:
        return [site_out(s) for s in load_sites().values()]

    return router
