import ZoneMap from "../components/ZoneMap";
import { Led, Panel, Tag } from "../components/ui";
import { LANGUAGE_NAMES, countdown } from "../format";
import type { Site, Zone } from "../types";
import type { Activity } from "../zoneAudio";

type Props = { sites: Site[]; zones: Zone[]; activity: Record<string, Activity>; now: number };

// The floor plans, one per site, with a readout per zone beside each: what is on air, what waits,
// and the next repeat. The light on the plan follows the clip this browser plays (zoneAudio.ts).
export default function Zones({ sites, zones, activity, now }: Props) {
  const names = Object.fromEntries(zones.map((z) => [z.zone_id, z.name]));
  return (
    <main className="space-y-4">
      {sites.map((site) => (
        <Panel key={site.id} title={site.name} right={<Tag>{site.hub_type}</Tag>}>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
            <ZoneMap siteId={site.id} activity={activity} zoneNames={names} />
            <ul className="space-y-2">
              {zones
                .filter((z) => z.site_id === site.id)
                .map((z) => (
                  <Readout key={z.zone_id} zone={z} activity={activity[z.zone_id]} now={now} />
                ))}
            </ul>
          </div>
        </Panel>
      ))}
    </main>
  );
}

function Readout({ zone, activity, now }: { zone: Zone; activity: Activity | undefined; now: number }) {
  const onAir = activity?.cmd ?? zone.on_air;
  const language = activity?.cmd.language ?? zone.on_air?.language;
  const caption = activity?.caption ?? zone.on_air?.caption ?? null;
  return (
    <li
      data-testid={`readout-${zone.zone_id}`}
      className={`rounded-sm border p-2 ${onAir ? "border-phosphor/60 bg-phosphor/5" : "border-ink-600 bg-ink-950"}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-semibold text-slate-100">
          <Led tone={onAir ? "phosphor" : "dim"} pulse={Boolean(onAir)} />
          {zone.name}
        </span>
        <span className="font-mono text-[10px] uppercase text-dim">{zone.languages.join(" · ")}</span>
      </div>
      {onAir ? (
        <div className="mt-1.5">
          <div className="font-mono text-[10px] uppercase tracking-wider text-phosphor">
            on air · event #{onAir.event_id} · {LANGUAGE_NAMES[language ?? ""] ?? language}
            {activity && !activity.audible && " · audio off"}
          </div>
          {caption && <p className="mt-0.5 text-xs leading-snug text-slate-200">{caption}</p>}
        </div>
      ) : (
        <p className="mt-1 font-mono text-[10px] uppercase tracking-wider text-dim">silent</p>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[10px] uppercase tracking-wider text-dim">
        <span>{zone.next ? `next #${zone.next.event_id} in ${countdown(zone.next.at, now)}` : "nothing scheduled"}</span>
        {zone.queue.length > 0 && <span className="text-info">{zone.queue.length} queued</span>}
        {zone.held > 0 && <span className="text-alert">{zone.held} awaiting approval</span>}
      </div>
    </li>
  );
}
