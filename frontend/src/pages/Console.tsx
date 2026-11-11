import EventCard from "../components/EventCard";
import { Led, Panel, Tag } from "../components/ui";
import { countdown, detail, headline } from "../format";
import { byUrgency, standing } from "../status";
import type { EventsData } from "../data";
import type { RelayEvent, Site, Zone } from "../types";
import type { Activity } from "../zoneAudio";

type Props = {
  events: EventsData;
  sites: Site[];
  zones: Zone[];
  activity: Record<string, Activity>;
  now: number;
  onApprove: (e: RelayEvent) => void;
  onEdit: (e: RelayEvent) => void;
  onStop: (e: RelayEvent) => void;
  onNew: () => void;
};

// The live board: every active event with its status, next play, scripts and the three actions,
// and a strip showing what each zone is doing right now.
export default function Console({ events, sites, zones, activity, now, onApprove, onEdit, onStop, onNew }: Props) {
  const rows = events.active
    .map((e) => ({ e, s: standing(e, zones, activity) }))
    .sort(byUrgency);
  const waiting = rows.filter((r) => r.s.status === "pending").length;

  return (
    <main className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-4">
        {events.error && (
          <p className="rounded-sm border border-alert/50 bg-alert/10 p-2 font-mono text-xs text-alert">{events.error}</p>
        )}
        <Panel
          title={`Live board · ${rows.length} active`}
          right={waiting > 0 ? <Tag tone="alert">{waiting} awaiting approval</Tag> : <Tag tone="ok">clear</Tag>}
        >
          {rows.length === 0 ? (
            <div className="py-10 text-center">
              <p className="font-mono text-xs uppercase tracking-[0.2em] text-dim">No active announcements</p>
              <p className="mt-2 text-sm text-dim">
                Run a scenario above, or{" "}
                <button className="text-phosphor underline-offset-2 hover:underline" onClick={onNew}>
                  write a new announcement
                </button>
                .
              </p>
            </div>
          ) : (
            <ul className="space-y-3">
              {rows.map(({ e, s }) => (
                <EventCard
                  key={e.id}
                  event={e}
                  standing={s}
                  site={sites.find((x) => x.id === e.site_id)}
                  now={now}
                  onApprove={() => onApprove(e)}
                  onEdit={() => onEdit(e)}
                  onStop={() => onStop(e)}
                />
              ))}
            </ul>
          )}
        </Panel>

        {events.recent.length > 0 && (
          <Panel title="Recently resolved">
            <ul className="divide-y divide-ink-600 text-sm">
              {events.recent.map((e) => (
                <li key={e.id} className="flex items-center justify-between gap-2 py-1.5">
                  <span className="min-w-0 truncate capitalize text-slate-400">
                    <span className="mr-2 font-mono text-[10px] text-dim">#{e.id}</span>
                    {headline(e)} <span className="text-dim">{detail(e)}</span>
                  </span>
                  <Tag tone={e.state === "withdrawn" ? "dim" : "ok"}>{e.state}</Tag>
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>

      <ZoneStrip sites={sites} zones={zones} activity={activity} now={now} />
    </main>
  );
}

/** Playing now / next, per zone. */
function ZoneStrip({
  sites,
  zones,
  activity,
  now,
}: {
  sites: Site[];
  zones: Zone[];
  activity: Record<string, Activity>;
  now: number;
}) {
  return (
    <Panel title="Zones" className="self-start lg:sticky lg:top-[132px]">
      <div className="space-y-3">
        {sites.map((site) => (
          <div key={site.id}>
            <h3 className="mb-1 font-mono text-[10px] uppercase tracking-[0.16em] text-dim">{site.name}</h3>
            <ul className="space-y-1">
              {zones
                .filter((z) => z.site_id === site.id)
                .map((z) => {
                  const a = activity[z.zone_id];
                  const onAir = a?.cmd ?? z.on_air;
                  return (
                    <li
                      key={z.zone_id}
                      data-testid={`strip-${z.zone_id}`}
                      data-lit={Boolean(onAir)}
                      className={`flex items-center justify-between gap-2 rounded-sm border px-2 py-1 text-xs ${
                        onAir ? "border-phosphor/60 bg-phosphor/10 text-phosphor" : "border-ink-600 text-slate-400"
                      }`}
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <Led tone={onAir ? "phosphor" : "dim"} pulse={Boolean(onAir)} />
                        <span className="truncate">{z.name}</span>
                      </span>
                      <span className="shrink-0 font-mono text-[10px] uppercase">
                        {onAir
                          ? `#${onAir.event_id} ${(a?.cmd.language ?? z.on_air?.language ?? "").toUpperCase()}`
                          : z.held > 0
                            ? `${z.held} held`
                            : z.next
                              ? `next ${countdown(z.next.at, now)}`
                              : "idle"}
                      </span>
                    </li>
                  );
                })}
            </ul>
          </div>
        ))}
      </div>
    </Panel>
  );
}
