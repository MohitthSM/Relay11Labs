import type { Activity } from "../zoneAudio";

// A schematic floor plan per site. Each zone is a shape that lights amber while a clip plays in
// it, with sound rings when this browser is really playing it. The drawing is front-end only: it
// knows zone ids, everything else comes from the server.

type Shape = { id: string; x: number; y: number; w: number; h: number; label: string; sub?: string };
type Decor = { x: number; y: number; w: number; h: number; label?: string; rx?: number };
type Plan = { title: string; zones: Shape[]; decor: Decor[]; lines?: [number, number, number, number][] };

const PLANS: Record<string, Plan> = {
  tube_station: {
    title: "Underground station",
    decor: [
      { x: 8, y: 96, w: 14, h: 100, rx: 2 }, // entrance doors
    ],
    lines: [
      // stairs from the hall up to both platforms
      [178, 120, 232, 70],
      [178, 172, 232, 222],
      // rails and a centre line
      [232, 118, 592, 118],
      [232, 124, 592, 124],
      [232, 168, 592, 168],
      [232, 174, 592, 174],
    ],
    zones: [
      { id: "ticket-hall", x: 22, y: 76, w: 156, h: 140, label: "Ticket hall" },
      { id: "jubilee-eb", x: 232, y: 28, w: 360, h: 78, label: "Jubilee eastbound", sub: "platform →" },
      { id: "jubilee-wb", x: 232, y: 186, w: 360, h: 78, label: "Jubilee westbound", sub: "← platform" },
    ],
  },
  airport: {
    title: "Terminal 5 · Concourse B",
    decor: [
      { x: 24, y: 34, w: 70, h: 74, label: "Gate 21", rx: 4 },
      { x: 104, y: 34, w: 70, h: 74, label: "Gate 22", rx: 4 },
      { x: 184, y: 34, w: 70, h: 74, label: "Gate 25", rx: 4 },
      { x: 496, y: 34, w: 70, h: 74, label: "Gate 24", rx: 4 },
      { x: 24, y: 214, w: 120, h: 56, label: "Security", rx: 4 },
      { x: 160, y: 214, w: 150, h: 56, label: "Shops", rx: 4 },
      { x: 326, y: 214, w: 110, h: 56, label: "Lounge", rx: 4 },
      { x: 452, y: 214, w: 120, h: 56, label: "Baggage", rx: 4 },
    ],
    lines: [],
    zones: [
      { id: "gate-23", x: 344, y: 30, w: 140, h: 82, label: "Gate 23", sub: "boarding" },
      { id: "concourse-b", x: 24, y: 122, w: 548, h: 78, label: "Concourse B", sub: "walkway" },
    ],
  },
  bus_station: {
    title: "Coach and bus station",
    decor: [
      { x: 24, y: 28, w: 88, h: 128, label: "Stand A", rx: 4 },
      { x: 216, y: 28, w: 88, h: 128, label: "Stand C", rx: 4 },
      { x: 312, y: 28, w: 88, h: 128, label: "Stand D", rx: 4 },
      { x: 408, y: 28, w: 88, h: 128, label: "Stand E", rx: 4 },
      { x: 24, y: 232, w: 90, h: 40, label: "Tickets", rx: 4 },
      { x: 482, y: 232, w: 90, h: 40, label: "Cafe", rx: 4 },
    ],
    lines: [],
    zones: [
      { id: "stand-b", x: 120, y: 28, w: 88, h: 128, label: "Stand B", sub: "bay" },
      { id: "stand-f", x: 504, y: 28, w: 88, h: 128, label: "Stand F", sub: "bay" },
      { id: "concourse", x: 24, y: 168, w: 568, h: 56, label: "Station concourse", sub: "walkway" },
    ],
  },
};

type Props = { siteId: string; activity: Record<string, Activity>; zoneNames: Record<string, string> };

export default function ZoneMap({ siteId, activity, zoneNames }: Props) {
  const plan = PLANS[siteId];
  if (!plan) return <p className="text-sm text-dim">No floor plan for {siteId}.</p>;
  return (
    <svg viewBox="0 0 616 296" role="img" aria-label={`${plan.title} zone map`} className="w-full">
      <rect x="1" y="1" width="614" height="294" rx="6" className="fill-ink-950 stroke-ink-600" />
      <text x="14" y="16" className="fill-dim font-mono text-[9px] uppercase tracking-[0.2em]">
        {plan.title}
      </text>
      {plan.decor.map((d, i) => (
        <g key={i}>
          <rect x={d.x} y={d.y} width={d.w} height={d.h} rx={d.rx ?? 2} className="fill-ink-800 stroke-ink-600" />
          {d.label && (
            <text
              x={d.x + d.w / 2}
              y={d.y + d.h / 2 + 3}
              textAnchor="middle"
              className="fill-ink-500 font-mono text-[9px] uppercase tracking-wider"
            >
              {d.label}
            </text>
          )}
        </g>
      ))}
      {plan.lines?.map(([x1, y1, x2, y2], i) => (
        <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} className="stroke-ink-500" strokeDasharray="5 4" />
      ))}
      {plan.zones.map((z) => (
        <Zone key={z.id} shape={z} activity={activity[z.id]} name={zoneNames[z.id] ?? z.label} />
      ))}
    </svg>
  );
}

function Zone({ shape: z, activity, name }: { shape: Shape; activity: Activity | undefined; name: string }) {
  const lit = Boolean(activity);
  // a narrow bay has no room beside its name: the speaker goes in the middle instead
  const narrow = z.w < 120;
  const cx = narrow ? z.x + z.w / 2 - 10 : z.x + z.w - 26;
  const cy = narrow ? z.y + z.h / 2 - 8 : z.y + 22;
  return (
    <g
      data-testid={`zone-${z.id}`}
      data-lit={lit}
      style={lit ? { filter: "drop-shadow(0 0 9px rgba(255,176,32,0.7))" } : undefined}
    >
      <title>{name}</title>
      <rect
        x={z.x}
        y={z.y}
        width={z.w}
        height={z.h}
        rx="5"
        className={lit ? "fill-phosphor/20 stroke-phosphor" : "fill-ink-700 stroke-ink-500"}
        strokeWidth={lit ? 2 : 1}
      />
      <text
        x={z.x + 10}
        y={z.y + 20}
        className={`font-mono text-[11px] font-bold uppercase tracking-wider ${lit ? "fill-phosphor" : "fill-slate-400"}`}
      >
        {z.label}
      </text>
      {z.sub && (
        <text x={z.x + 10} y={z.y + 34} className="fill-dim font-mono text-[9px] uppercase tracking-widest">
          {z.sub}
        </text>
      )}
      {lit && activity && (
        <>
          {/* a speaker and three sound rings that pulse in turn */}
          <path
            d={`M${cx - 9} ${cy - 4} h5 l6 -5 v18 l-6 -5 h-5 z`}
            className="fill-phosphor"
            transform="translate(-4 -2)"
          />
          {[0, 1, 2].map((i) => (
            <path
              key={i}
              d={`M${cx + 6 + i * 6} ${cy - 5 - i * 3} q${4 + i * 3} ${5 + i * 3} 0 ${10 + i * 6}`}
              className="animate-ring fill-none stroke-phosphor"
              strokeWidth="1.6"
              strokeLinecap="round"
              style={{ animationDelay: `${i * 0.25}s` }}
            />
          ))}
          <text
            x={z.x + 10}
            y={z.y + z.h - 10}
            className="fill-phosphor font-mono text-[9px] font-semibold uppercase tracking-wider"
          >
            on air · {activity.cmd.language}
            {activity.audible ? "" : " · muted"}
          </text>
        </>
      )}
    </g>
  );
}
