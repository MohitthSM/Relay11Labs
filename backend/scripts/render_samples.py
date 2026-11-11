"""Render the pitch samples with real audio, and the v4 Turbo vs v4 comparison.

    uv run python scripts/render_samples.py                 # needs ELEVENLABS_API_KEY in .env
    uv run python scripts/render_samples.py --out /tmp/x    # mock mode allowed (WAV, not samples)

What it writes to --out (default ../docs/samples):
  b_<lang>_u<level>.mp3     scenario B: the 9 clips (EN, ES, FR x levels 1-3), as the app makes them
  a_v1_severe.mp3, a_v2_minor.mp3, a_resolved.mp3   scenario A: the three versions, EN
  compare_b_<lang>_u1_<model>.mp3   the same scenario B level-1 script on each model, no chime
  report.md                 per clip: model, characters billed, cost, time to first audio, total
Scripts come from the real script engine (Claude if ANTHROPIC_API_KEY is set, else templates),
voice from the real voice layer, against a throwaway database, so nothing touches data/.
The voice cache is per run: every clip here is a cold render, which is what TTFA should measure.
Listen to the compare_* files before deciding D17's routing; the script records numbers only.
"""

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session

from relay import timeutil, voice
from relay.config import Settings, get_settings
from relay.db import init_db, make_engine
from relay.providers import build_llm, build_tts
from relay.providers.tts_base import TTSProvider
from relay.schemas import EventInput
from relay.script.engine import build_context, generate
from relay.sites import Site, load_sites

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "scenarios"
DEFAULT_OUT = ROOT.parent / "docs" / "samples"


@dataclass
class Row:
    file: str
    language: str
    urgency: int
    model: str
    source: str  # llm | template
    characters: int
    cost_usd: float
    ttfa_ms: float
    total_ms: float


def payloads(name: str) -> list[dict]:
    steps = json.loads((SCENARIOS / f"{name}.json").read_text(encoding="utf-8"))["events"]
    return [s["payload"] for s in steps]


def render(
    session: Session,
    tts: TTSProvider,
    settings: Settings,
    site: Site,
    jobs: list[tuple[str, voice.Item, str]],
    out: Path,
) -> list[Row]:
    """Voice each (file stem, item, script source) and copy its clip to out/<stem>.<ext>."""
    rows = []
    for stem, item, source in jobs:
        [r] = voice.render_many(session, tts, settings, site, [item])
        if r.error or r.path is None:
            print(f"  {stem}: FAILED ({r.error})")
            continue
        name = f"{stem}{r.path.suffix}"
        shutil.copyfile(r.path, out / name)
        rows.append(
            Row(name, item.language, item.urgency or 1, r.model_id, source,
                r.characters_billed, r.cost_usd, r.ttfa_ms, r.total_ms)
        )  # fmt: skip
        print(f"  {name}: {r.characters_billed} chars, ${r.cost_usd:.4f}, ttfa {r.ttfa_ms:.0f} ms")
    return rows


def script_items(
    event: EventInput, site: Site, llm, settings: Settings, *, previous=None, resolution=False
):
    context = build_context(site, event.type, previous=previous)
    plan = generate(event, context, llm, settings, now=timeutil.now())
    first = context.languages[0]
    for s in plan.scripts:
        kind = voice.render_kind(s.urgency, is_resolution=resolution)
        item = voice.Item(s.spoken_text, s.language, kind, chime=s.language == first,
                          urgency=s.urgency)  # fmt: skip
        yield s, item


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    base = get_settings()
    tts, llm = build_tts(base), build_llm(base)
    if tts.mode != "live" and args.out.resolve() == DEFAULT_OUT.resolve():
        print("TTS is in mock mode: mock WAVs are not samples. Set ELEVENLABS_API_KEY, or --out.")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    print(f"LLM {llm.mode}, TTS {tts.mode}; writing to {args.out}")

    with tempfile.TemporaryDirectory() as tmp:
        settings = base.model_copy(update={"audio_dir": f"{tmp}/audio"})
        engine = make_engine(f"sqlite:///{tmp}/samples.db")
        init_db(engine)
        sites = load_sites()
        rows: list[Row] = []
        with Session(engine) as session:
            print("scenario B")
            airport = sites["airport"]
            page = EventInput.of(event_key="b", site_id="airport", payload=payloads("b_ba117")[0])
            b = [(f"b_{s.language}_u{s.urgency}", item, s.source)
                 for s, item in script_items(page, airport, llm, settings)]  # fmt: skip
            rows += render(session, tts, settings, airport, b, args.out)

            print("scenario A")
            tube = sites["tube_station"]
            severe, minor, good = payloads("a_jubilee")
            v1 = EventInput.of(event_key="a", site_id="tube_station", payload=severe)
            v2 = EventInput.of(event_key="a", site_id="tube_station", payload=minor, version=2)
            res = EventInput.of(event_key="a", site_id="tube_station", payload=good)
            first = list(script_items(v1, tube, llm, settings))
            a = [("a_v1_severe", first[0][1], first[0][0].source)]
            for s, item in script_items(v2, tube, llm, settings, previous=[first[0][0].draft]):
                a.append(("a_v2_minor", item, s.source))
            for s, item in script_items(res, tube, llm, settings, resolution=True):
                a.append(("a_resolved", item, s.source))
            rows += render(session, tts, settings, tube, a, args.out)

            print("model comparison: scenario B level 1, same script, no chime")
            for model in (base.elevenlabs_model_live, base.elevenlabs_model_scheduled):
                per_model = settings.model_copy(  # own audio dir: a cold render, never a hit
                    update={"elevenlabs_model_live": model, "audio_dir": f"{tmp}/audio-{model}"}
                )
                cmp = [(f"compare_b_{s.language}_u1_{model}", voice.Item(item.text, item.language),
                        s.source)
                       for s, item in script_items(page, airport, llm, settings)
                       if s.urgency == 1]  # fmt: skip
                rows += render(session, tts, per_model, airport, cmp, args.out)

    write_report(args.out / "report.md", rows, llm.mode, tts.mode)
    print(f"report: {args.out / 'report.md'}")
    return 0


def write_report(path: Path, rows: list[Row], llm_mode: str, tts_mode: str) -> None:
    lines = [
        "# Sample clips",
        "",
        f"Generated by `scripts/render_samples.py` (LLM {llm_mode}, TTS {tts_mode}). Cold cache:",
        "every clip is a fresh render. TTFA = request start to first audio byte.",
        "",
        "| File | Lang | Level | Model | Script | Chars | Cost (USD) | TTFA (ms) | Total (ms) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.file} | {r.language} | {r.urgency} | {r.model} | {r.source} | {r.characters} "
            f"| {r.cost_usd:.4f} | {r.ttfa_ms:.0f} | {r.total_ms:.0f} |"
        )
    lines += ["", f"Total: {sum(r.characters for r in rows)} characters, "
              f"${sum(r.cost_usd for r in rows):.4f}."]  # fmt: skip
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
