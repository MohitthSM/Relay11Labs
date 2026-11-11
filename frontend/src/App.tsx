import { useCallback, useEffect, useState } from "react";
import {
  approveEvent,
  getHealth,
  getRun,
  getSites,
  getSource,
  listScenarios,
  resolveEvent,
  runScenario,
  setHallucinate,
  setSource,
  setSpeed,
} from "./api";
import Compose from "./components/Compose";
import { BTN, BTN_PRIMARY, Led } from "./components/ui";
import { useEvents, useNow, useZones } from "./data";
import { clock, headline } from "./format";
import { useLive } from "./live";
import Console from "./pages/Console";
import Metrics from "./pages/Metrics";
import Zones from "./pages/Zones";
import type { DataMode, Health, RelayEvent, RunStatus, ScenarioInfo, Site, SourceStatus } from "./types";
import { useZoneAudio } from "./zoneAudio";

type Page = "console" | "zones" | "metrics";
const PAGES: [Page, string][] = [
  ["console", "Live board"],
  ["zones", "Zone map"],
  ["metrics", "Metrics"],
];
const SPEEDS = [1, 10, 60, 600];

export default function App() {
  const live = useLive();
  const events = useEvents(live);
  const zones = useZones(live);
  const now = useNow();

  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [sites, setSites] = useState<Site[]>([]);
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const [runs, setRuns] = useState<Record<string, RunStatus>>({});
  const [page, setPage] = useState<Page>("console");
  const [operator, setOperator] = useState("operator"); // recorded as approved_by (D20)
  const [speed, setSpeedState] = useState(60);
  const [autoApprove, setAutoApprove] = useState(false);
  const [audioOn, setAudioOn] = useState(false);
  const [compose, setCompose] = useState<{ edit: RelayEvent | null } | null>(null);
  const [notice, setNotice] = useState<{ text: string; bad: boolean } | null>(null);

  const [source, setSourceState] = useState<SourceStatus | null>(null);

  const activity = useZoneAudio(live, audioOn, speed);

  const tell = useCallback((text: string, bad = false) => setNotice({ text, bad }), []);
  const failed = useCallback((e: unknown) => tell((e as Error).message, true), [tell]);

  useEffect(() => {
    getHealth().then(setHealth).catch((e: Error) => setHealthError(e.message));
    getSites().then(setSites).catch(failed);
    listScenarios()
      .then((s) => {
        setScenarios(s.scenarios);
        setSpeedState(s.speed);
      })
      .catch(failed);
  }, [failed]);

  // the feed status arrives on the same WebSocket (D8); a reconnect re-fetches what was missed
  useEffect(() => {
    const load = () => getSource().then(setSourceState).catch(() => {});
    load();
    const unsubscribe = live.subscribe((m) => {
      if (m.type === "source") setSourceState((prev) => (prev ? { ...prev, ...m } : prev));
    });
    return unsubscribe;
  }, [live, live.status]);

  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), notice.bad ? 8000 : 4000);
    return () => window.clearTimeout(t);
  }, [notice]);

  // follow the scenarios that are running until they finish
  const running = Object.values(runs).filter((r) => r.state === "running");
  useEffect(() => {
    if (running.length === 0) return;
    const t = window.setInterval(() => {
      for (const r of running) {
        getRun(r.run_id)
          .then((next) => setRuns((prev) => ({ ...prev, [next.scenario]: next })))
          .catch(() => {});
      }
    }, 400);
    return () => window.clearInterval(t);
  }, [running.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const liveMode = source?.mode === "live";
  const changeSource = (mode: DataMode) =>
    setSource(mode).then(setSourceState).catch(failed);

  const start = (name: string) =>
    runScenario(name, speed, autoApprove)
      .then((r) => setRuns((prev) => ({ ...prev, [name]: r })))
      .catch(failed);

  const changeSpeed = (scale: number) => {
    setSpeedState(scale);
    setSpeed(scale).catch(failed);
  };

  const toggleHallucinate = (enabled: boolean) => {
    const show = (value: boolean) => setHealth((h) => (h ? { ...h, hallucinate: value } : h));
    show(enabled); // the checkbox answers at once; it goes back if the server refuses
    setHallucinate(enabled).catch((e) => {
      show(!enabled);
      failed(e);
    });
  };

  const approve = (e: RelayEvent) =>
    approveEvent(e.id, operator).then(events.refresh).catch(failed);
  const stop = (e: RelayEvent) => resolveEvent(e.id).then(events.refresh).catch(failed);

  const done = (e: RelayEvent) => {
    setCompose(null);
    events.refresh();
    setPage("console");
    tell(
      e.approval === "pending"
        ? `Event #${e.id} v${e.version} is waiting for approval: ${headline(e)}`
        : `Event #${e.id} v${e.version} broadcast: ${headline(e)}`,
    );
  };

  const socketTone = live.status === "open" ? "ok" : live.status === "connecting" ? "phosphor" : "alert";

  return (
    <div className="mx-auto max-w-[1500px] px-4 pb-10">
      <div className="sticky top-0 z-30 -mx-4 border-b border-ink-600 bg-ink-950/95 px-4 backdrop-blur">
        <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 py-2.5">
          <div className="flex items-baseline gap-3">
            <h1 className="font-mono text-lg font-bold tracking-[0.3em] text-phosphor">RELAY</h1>
            <span className="hidden font-mono text-[10px] uppercase tracking-[0.2em] text-dim sm:inline">PA control</span>
          </div>
          <nav className="flex gap-1" aria-label="Pages">
            {PAGES.map(([p, label]) => (
              <button
                key={p}
                onClick={() => setPage(p)}
                data-testid={`nav-${p}`}
                className={`rounded-sm border px-3 py-1 font-mono text-[11px] font-semibold uppercase tracking-wider ${
                  page === p
                    ? "border-phosphor/60 bg-phosphor/10 text-phosphor"
                    : "border-transparent text-dim hover:text-slate-200"
                }`}
              >
                {label}
              </button>
            ))}
          </nav>
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-sm tabular-nums text-slate-200" data-testid="clock">
              {clock(new Date(now))}
            </span>
            <span
              className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-dim"
              title={`WebSocket ${live.status}`}
              data-testid="socket"
              data-status={live.status}
            >
              <Led tone={socketTone} pulse={live.status !== "open"} />
              {live.status === "open" ? "live" : live.status}
            </span>
            <span className="rounded-sm border border-ink-500 bg-ink-800 px-2 py-1 font-mono text-[11px] text-slate-300" data-testid="health">
              {health ? `LLM: ${health.llm} · TTS: ${health.tts}` : (healthError ?? "connecting…")}
            </span>
            <label className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-dim">
              operator
              <input
                className="w-24 rounded-sm border border-ink-500 bg-ink-950 px-1.5 py-0.5 text-xs text-slate-100 outline-none focus:border-phosphor"
                value={operator}
                onChange={(e) => setOperator(e.target.value)}
              />
            </label>
            <button
              data-testid="audio"
              aria-pressed={audioOn}
              onClick={() => setAudioOn(!audioOn)} // a click is needed before a browser plays audio
              className={
                audioOn
                  ? "rounded-sm border border-phosphor bg-phosphor/15 px-2.5 py-1 font-mono text-[11px] font-bold uppercase tracking-wider text-phosphor"
                  : BTN
              }
            >
              {audioOn ? "🔊 Audio on" : "🔇 Enable audio"}
            </button>
          </div>
        </header>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-ink-700 py-2" data-testid="toolbar">
          <div className="flex" role="group" aria-label="Data source" data-testid="source">
            {(["demo", "live"] as DataMode[]).map((m) => (
              <button
                key={m}
                data-testid={`source-${m}`}
                aria-pressed={source?.mode === m}
                disabled={!source}
                onClick={() => source?.mode !== m && changeSource(m)}
                title={m === "demo" ? "Scripted scenarios. Switching here withdraws live TfL announcements" : "Real TfL line status, polled every minute"}
                className={`border px-2.5 py-1 font-mono text-[11px] font-bold uppercase tracking-wider first:rounded-l-sm last:rounded-r-sm ${
                  source?.mode === m
                    ? "border-phosphor bg-phosphor/15 text-phosphor"
                    : "border-ink-500 bg-ink-800 text-dim hover:text-slate-200"
                }`}
              >
                {m === "demo" ? "Demo (scripted)" : "Live (TfL)"}
              </button>
            ))}
          </div>
          {liveMode && (
            <span className="font-mono text-[10px] uppercase tracking-wider text-dim" data-testid="feed-status" data-status={source?.status}>
              feed: {source?.status}
              {source && source.lines.length > 0 && ` · ${source.lines.map((l) => `${l.name} ${l.status ?? "n/a"}`).join(", ")}`}
              {source && !source.app_key && " · no app key"}
            </span>
          )}
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-dim">Run scenario</span>
          <div className="flex gap-1.5">
            {scenarios.map((s) => {
              const r = runs[s.name];
              const letter = s.name.charAt(0).toUpperCase();
              return (
                <button
                  key={s.name}
                  data-testid={`run-${letter}`}
                  title={s.description}
                  disabled={r?.state === "running" || liveMode}
                  className={BTN}
                  onClick={() => start(s.name)}
                >
                  {letter} · {s.name.slice(2)}
                  {r?.state === "running" && <span className="ml-1.5 text-phosphor">{r.steps_total ? `${r.steps_done}/${r.steps_total}` : "…"}</span>}
                  {r?.state === "failed" && <span className="ml-1.5 text-alert">failed</span>}
                </button>
              );
            })}
          </div>
          <label className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-dim" title="One minute of scenario time passes in 60/speed seconds">
            speed
            <select
              data-testid="speed"
              className="rounded-sm border border-ink-500 bg-ink-950 px-1.5 py-0.5 text-xs text-slate-100"
              value={speed}
              onChange={(e) => changeSpeed(Number(e.target.value))}
            >
              {[...new Set([...SPEEDS, speed])].sort((a, b) => a - b).map((s) => (
                <option key={s} value={s}>
                  {s}x
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-dim" title="Approve held events as 'scenario runner'; the audit log says so">
            <input type="checkbox" className="accent-amber-400" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} />
            auto-approve
          </label>
          {health?.hallucinate != null && (
            <label className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-dim" title="Mock LLM only: it writes one false fact per event, so you can watch the fact lock reject it">
              <input
                type="checkbox"
                data-testid="hallucinate"
                className="accent-red-400"
                checked={health.hallucinate}
                onChange={(e) => toggleHallucinate(e.target.checked)}
              />
              hallucinate
            </label>
          )}
          <button className={`${BTN_PRIMARY} ml-auto`} data-testid="new" onClick={() => setCompose({ edit: null })} disabled={sites.length === 0}>
            + New announcement
          </button>
        </div>
      </div>

      {liveMode && source?.warning && (
        <div role="alert" data-testid="feed-warning" className="mt-3 rounded-sm border border-alert/60 bg-alert/10 px-3 py-2 font-mono text-xs text-alert">
          ⚠ {source.warning}
        </div>
      )}
      {source?.mode === "live" && Object.keys(source.line_errors).length > 0 && (
        <div className="mt-3 rounded-sm border border-amber-400/50 bg-amber-400/10 px-3 py-2 font-mono text-xs text-amber-300" data-testid="feed-line-errors">
          Not applied: {Object.entries(source.line_errors).map(([k, v]) => `${k} (${v})`).join("; ")}
        </div>
      )}

      <div className="mt-4">
        {page === "console" && (
          <Console
            events={events}
            sites={sites}
            zones={zones}
            activity={activity}
            now={now}
            onApprove={approve}
            onEdit={(e) => setCompose({ edit: e })}
            onStop={stop}
            onNew={() => setCompose({ edit: null })}
          />
        )}
        {page === "zones" && <Zones sites={sites} zones={zones} activity={activity} now={now} />}
        {page === "metrics" && <Metrics live={live} />}
      </div>

      {compose && <Compose sites={sites} edit={compose.edit} onClose={() => setCompose(null)} onDone={done} />}

      {notice && (
        <div
          role="status"
          data-testid="notice"
          className={`fixed bottom-4 right-4 z-50 max-w-sm animate-slidein rounded-sm border px-3 py-2 font-mono text-xs shadow-xl ${
            notice.bad ? "border-alert/60 bg-ink-800 text-alert" : "border-ok/50 bg-ink-800 text-ok"
          }`}
        >
          {notice.text}
        </div>
      )}
    </div>
  );
}
