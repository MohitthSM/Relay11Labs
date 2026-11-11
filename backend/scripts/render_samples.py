"""Render the pitch samples with real ElevenLabs audio, within a character budget.

    uv run python scripts/render_samples.py          # the plan: characters per clip, no API call
    uv run python scripts/render_samples.py --go     # render what the plan says, then commit
                                                     # docs/samples/ (MP3s + manifest.json)

The sample set (kept small for a free-tier key):
  b_en_u1, b_en_u3, b_es_u1, b_fr_u1   scenario B, the page at urgency 1 (EN, ES, FR) and 3 (EN)
  a_v1_en                              scenario A, the first announcement (EN)
  compare_b_en_u1_<model>              the same short EN script on each TTS model: the v4 Turbo
                                       side is b_en_u1's own speech, so only the v4 side is new
Scripts come from the real script engine (Claude when ANTHROPIC_API_KEY is set, else the
templates), voice from the real voice layer.

Never paid twice: every clip ever rendered is listed in docs/samples/manifest.json and its file
is committed. The cache is seeded from it before planning, so a clip already there costs 0, and
the app seeds the same clips at start-up (voice.seed_cache), so the demo replays them for free.
The manifest's ledger records the characters of every run (and of earlier spending, like the
live smoke test); the script stops before a run that would pass --budget. Characters are what
ElevenLabs bills; this counts one credit per character, which can only overstate.
"""

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session

from relay import timeutil, voice
from relay.config import Settings, get_settings
from relay.db import init_db, make_engine
from relay.models import AudioClip
from relay.providers import build_llm, build_tts
from relay.schemas import EventInput
from relay.script.engine import build_context, generate
from relay.sites import Site, load_sites

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "scenarios"
DEFAULT_OUT = ROOT.parent / "docs" / "samples"
B_SLOTS = [("en", 1), ("en", 3), ("es", 1), ("fr", 1)]
# --all-b: the rest of scenario B, so the whole page (D4: every level and language at ingest)
# plays from the cache
B_REST = [("en", 2), ("es", 2), ("fr", 2), ("es", 3), ("fr", 3)]


@dataclass
class Job:
    stem: str
    site: Site
    item: voice.Item
    source: str  # llm | template
    settings: Settings


def first_payload(name: str, step: int = 0) -> dict:
    data = json.loads((SCENARIOS / f"{name}.json").read_text(encoding="utf-8"))
    return data["events"][step]["payload"]


def scripts(event: EventInput, site: Site, llm, settings: Settings):
    context = build_context(site, event.type)
    plan = generate(event, context, llm, settings, now=timeutil.now())
    return context.languages[0], plan.scripts


def build_jobs(settings: Settings, llm, all_b: bool = False) -> list[Job]:
    sites = load_sites()
    airport, tube = sites["airport"], sites["tube_station"]
    jobs: list[Job] = []

    page = EventInput.of(event_key="b", site_id="airport", payload=first_payload("b_ba117"))
    first, slots = scripts(page, airport, llm, settings)
    by_slot = {(s.language, s.urgency): s for s in slots}
    for lang, urgency in B_SLOTS + (B_REST if all_b else []):
        s = by_slot[(lang, urgency)]
        item = voice.Item(s.spoken_text, lang, voice.render_kind(urgency),
                          chime=lang == first, urgency=urgency)  # fmt: skip
        jobs.append(Job(f"b_{lang}_u{urgency}", airport, item, s.source, settings))

    en1 = by_slot[("en", 1)]  # the comparison: b_en_u1's speech without the chime, both models
    live, scheduled = settings.elevenlabs_model_live, settings.elevenlabs_model_scheduled
    plain = voice.Item(en1.spoken_text, "en", "live", urgency=1)
    jobs.append(Job(f"compare_b_en_u1_{live}", airport, plain, en1.source, settings))
    on_v4 = settings.model_copy(update={"elevenlabs_model_live": scheduled})
    jobs.append(Job(f"compare_b_en_u1_{scheduled}", airport, plain, en1.source, on_v4))

    severe_payload = first_payload("a_jubilee")
    severe = EventInput.of(event_key="a", site_id="tube_station", payload=severe_payload)
    first, slots = scripts(severe, tube, llm, settings)
    s = next(x for x in slots if (x.language, x.urgency) == ("en", 1))
    item = voice.Item(s.spoken_text, "en", "live", chime=True, urgency=1)
    jobs.append(Job("a_v1_en", tube, item, s.source, settings))
    return jobs


def plan(session: Session, tts, jobs: list[Job]) -> list[int]:
    """Characters each job would bill, in order. Jobs that share settings and site are planned
    as one batch, so a segment two jobs need is counted once."""
    out = [0] * len(jobs)
    groups: dict[tuple[int, str], list[int]] = {}
    for i, job in enumerate(jobs):
        groups.setdefault((id(job.settings), job.site.id), []).append(i)
    for indexes in groups.values():
        job0 = jobs[indexes[0]]
        items = [jobs[i].item for i in indexes]
        for i, chars in zip(indexes, voice.characters_to_bill(
            session, tts, job0.settings, job0.site, items), strict=True):  # fmt: skip
            out[i] = chars
    return out


def export(session: Session, out_dir: Path, manifest: dict, done: list[tuple[Job, str]]) -> None:
    """Add every new real clip to out_dir and the manifest: the clip each job plays under the
    job's name, then the segments stitched into them under cache/<id>."""
    known = {c["id"] for c in manifest["clips"]}

    def add(clip: AudioClip, file: str) -> None:
        src = voice.clip_path(_settings_of(done), clip)
        (out_dir / file).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out_dir / file)
        manifest["clips"].append({
            "id": clip.id, "file": file, "kind": clip.kind, "permanent": clip.permanent,
            "provider": clip.provider, "model_id": clip.model_id, "voice_id": clip.voice_id,
            "language": clip.language, "characters": clip.characters,
            "cost_usd": round(clip.cost_usd, 6), "duration_s": round(clip.duration_s, 3),
            "parts": clip.parts,
        })  # fmt: skip
        known.add(clip.id)

    for job, clip_id in done:
        clip = session.get(AudioClip, clip_id)
        if clip is not None and clip.id not in known:
            add(clip, f"{job.stem}{Path(clip.path).suffix}")
    for job, clip_id in done:  # a job whose clip was already committed under another name
        clip = session.get(AudioClip, clip_id)
        if clip is not None and not (out_dir / f"{job.stem}{Path(clip.path).suffix}").exists():
            shutil.copyfile(voice.clip_path(job.settings, clip),
                            out_dir / f"{job.stem}{Path(clip.path).suffix}")  # fmt: skip
    for _, clip_id in done:
        clip = session.get(AudioClip, clip_id)
        for part_id in clip.parts if clip else []:
            part = session.get(AudioClip, part_id)
            if part is not None and part.id not in known:
                add(part, f"cache/{part.id}{Path(part.path).suffix}")


def _settings_of(done: list[tuple[Job, str]]) -> Settings:
    return done[0][0].settings  # every job shares one audio_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--go", action="store_true", help="render (default: show the plan)")
    parser.add_argument("--budget", type=int, default=5000, help="characters, all runs together")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", default="", help="comma-separated clip names, e.g. b_en_u1")
    parser.add_argument("--all-b", action="store_true", help="add the rest of scenario B")
    args = parser.parse_args()
    base = get_settings()
    tts, llm = build_tts(base), build_llm(base)
    if tts.mode != "live" and args.out.resolve() == DEFAULT_OUT.resolve():
        print("TTS is in mock mode: mock audio is not a sample. Set ELEVENLABS_API_KEY, or --out.")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = voice.read_manifest(args.out)
    spent = sum(run["total"] for run in manifest["ledger"])
    print(f"LLM {llm.mode}, TTS {tts.mode}; out {args.out}")

    with tempfile.TemporaryDirectory() as tmp:
        settings = base.model_copy(update={"audio_dir": f"{tmp}/audio"})
        engine = make_engine(f"sqlite:///{tmp}/samples.db")
        init_db(engine)
        with Session(engine) as session:
            voice.seed_cache(session, settings, args.out)
            jobs = build_jobs(settings, llm, args.all_b)
            if llm.mode == "mock":  # the mock LLM returns the template wording
                for job in jobs:
                    job.source = "template (mock LLM)" if job.source == "llm" else job.source
            if args.only:
                wanted = {x.strip() for x in args.only.split(",") if x.strip()}
                unknown = wanted - {j.stem for j in jobs}
                if unknown:
                    print(f"unknown clip(s): {', '.join(sorted(unknown))}")
                    return 2
                jobs = [j for j in jobs if j.stem in wanted]
            planned = plan(session, tts, jobs)
            print(f"\n{'clip':<34} {'model':<16} {'chars':>6}")
            for job, chars in zip(jobs, planned, strict=True):
                model = voice.route_model(job.item.kind, job.settings)
                note = "  (cached)" if chars == 0 else ""
                print(f"{job.stem:<34} {model:<16} {chars:>6}{note}")
            total = sum(planned)
            print(f"\nthis run: {total} chars; spent before: {spent}; after: {spent + total} "
                  f"of {args.budget}")  # fmt: skip
            if spent + total > args.budget:
                print("over budget: nothing rendered")
                return 3
            if not args.go:
                print("plan only: run with --go to render")
                return 0

            done, rows = [], []
            for job in jobs:
                [r] = voice.render_many(session, tts, job.settings, job.site, [job.item])
                if r.error or r.clip_id is None:
                    print(f"  {job.stem}: FAILED ({r.error})")
                    continue
                done.append((job, r.clip_id))
                rows.append({"clip": job.stem, "model": r.model_id, "script": job.source,
                             "characters": r.characters_billed, "ttfa_ms": round(r.ttfa_ms),
                             "total_ms": round(r.total_ms)})  # fmt: skip
                print(f"  {job.stem}: {r.characters_billed} chars, ttfa {r.ttfa_ms:.0f} ms, "
                      f"total {r.total_ms:.0f} ms")  # fmt: skip
            run_total = sum(r["characters"] for r in rows)
            manifest["ledger"].append({"at": datetime.now(UTC).isoformat(timespec="seconds"),
                                       "what": "render_samples.py", "clips": rows,
                                       "total": run_total})  # fmt: skip
            export(session, args.out, manifest, done)
    (args.out / voice.MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", "utf-8")
    write_report(args.out / "report.md", manifest)
    print(f"\nthis run billed {run_total} chars; total so far {spent + run_total} of {args.budget}")
    return 0


def write_report(path: Path, manifest: dict) -> None:
    lines = [
        "# Sample clips: characters used",
        "",
        "Written by `scripts/render_samples.py` from `manifest.json`. Characters are what",
        "ElevenLabs bills (counted here as one credit each, an upper bound). TTFA = request start",
        "to first audio byte; 0 characters means the clip was already in the cache.",
        "",
    ]
    for run in manifest["ledger"]:
        lines += [f"## {run['at']}: {run['what']}", ""]
        if run.get("clips"):
            lines += ["| Clip | Model | Script | Chars | TTFA (ms) | Total (ms) |",
                      "|---|---|---|---|---|---|"]  # fmt: skip
            lines += [f"| {c['clip']} | {c['model']} | {c['script']} | {c['characters']} "
                      f"| {c['ttfa_ms'] or 'n/a'} | {c['total_ms'] or 'n/a'} |"
                      for c in run["clips"]]  # fmt: skip
            lines.append("")
        lines += [f"Run total: **{run['total']}** characters.", ""]
    total = sum(r["total"] for r in manifest["ledger"])
    lines.append(f"**Total so far: {total} characters.**")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
