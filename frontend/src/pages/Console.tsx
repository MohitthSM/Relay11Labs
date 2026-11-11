import { useCallback, useEffect, useState } from "react";
import { getEvent, listEvents, listScenarios, resolveEvent, runScenario, watchEvents } from "../api";
import AnnouncementCard from "../components/AnnouncementCard";
import type { RelayEvent, ScenarioInfo } from "../types";

const SPEEDS = [1, 10, 60, 600];

function describe(e: RelayEvent): string {
  const p = e.payload as Record<string, string | undefined>;
  if (e.type === "line_status") return `${p.line} line: ${p.status?.replace("_", " ")}`;
  if (e.type === "passenger_page") return `Page for ${p.passenger_name}, ${p.flight_number}, gate ${p.gate}`;
  return `Route ${p.route}: stand ${p.from_stand} to ${p.to_stand}`;
}

export default function Console() {
  const [events, setEvents] = useState<RelayEvent[]>([]);
  const [open, setOpen] = useState<RelayEvent | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const [speed, setSpeed] = useState(60);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listEvents().then(setEvents).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    refresh();
    listScenarios().then((s) => setScenarios(s.scenarios)).catch((e: Error) => setError(e.message));
    return watchEvents(() => refresh()); // any state change: re-fetch the list
  }, [refresh]);

  const show = (id: number) => getEvent(id).then(setOpen).catch((e: Error) => setError(e.message));

  return (
    <main className="space-y-4">
      {error && <p className="rounded bg-red-100 p-2 text-sm text-red-800">{error}</p>}

      <section className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">Run scenario</span>
        {scenarios.map((s) => (
          <button
            key={s.name}
            title={s.description}
            className="rounded bg-slate-800 px-3 py-1 text-white hover:bg-slate-700"
            onClick={() => runScenario(s.name, speed).catch((e: Error) => setError(e.message))}
          >
            {s.name}
          </button>
        ))}
        <label className="ml-2">
          speed{" "}
          <select className="rounded border p-1" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {SPEEDS.map((s) => (
              <option key={s} value={s}>
                {s}x
              </option>
            ))}
          </select>
        </label>
      </section>

      <section>
        <h2 className="mb-2 font-medium">Events</h2>
        {events.length === 0 && <p className="text-sm text-slate-500">No events yet. Run a scenario.</p>}
        <ul className="space-y-1">
          {events.map((e) => (
            <li key={e.id} className="flex items-center justify-between rounded border bg-white p-2 text-sm">
              <button className="text-left hover:underline" onClick={() => show(e.id)}>
                <span className="font-mono text-xs text-slate-500">#{e.id} v{e.version}</span> {describe(e)}
              </button>
              <span className="flex items-center gap-2">
                <span
                  className={`rounded px-2 text-xs ${
                    e.state === "active" ? "bg-green-100" : e.state === "resolved" ? "bg-slate-200" : "bg-amber-100"
                  }`}
                >
                  {e.state}
                </span>
                {e.state === "active" && (
                  <button
                    className="rounded border px-2 text-xs hover:bg-slate-100"
                    onClick={() => resolveEvent(e.id).then(refresh).catch((x: Error) => setError(x.message))}
                  >
                    Resolve
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      </section>

      {open && (
        <section>
          <h2 className="mb-2 font-medium">
            Event #{open.id}: {open.announcements.length} announcements
          </h2>
          <ul className="grid gap-2 md:grid-cols-2">
            {open.announcements.map((a) => (
              <AnnouncementCard key={a.id} a={a} />
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
