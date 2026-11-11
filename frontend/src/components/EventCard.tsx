import { useEffect, useRef, useState } from "react";
import { audioSrc } from "../api";
import { LANGUAGE_NAMES, TYPE_LABELS, countdown, detail, headline } from "../format";
import type { EventStanding, Status } from "../status";
import type { Announcement, RelayEvent, Site } from "../types";
import { BTN, BTN_ALERT, Led, Tag } from "./ui";
import type { Tone } from "./ui";

const STATUS: Record<Status, { label: string; tone: Tone }> = {
  pending: { label: "Pending approval", tone: "alert" },
  playing: { label: "Playing", tone: "phosphor" },
  scheduled: { label: "Scheduled", tone: "info" },
  played: { label: "Played out", tone: "dim" },
  resolved: { label: "Resolved", tone: "ok" },
};

type Props = {
  event: RelayEvent;
  standing: EventStanding;
  site: Site | undefined;
  now: number;
  onApprove: () => void;
  onEdit: () => void;
  onStop: () => void;
};

export default function EventCard({ event, standing, site, now, onApprove, onEdit, onStop }: Props) {
  const { status, nextAt, playingIn } = standing;
  const s = STATUS[status];
  const zones = site?.policies.find((p) => p.event_type === event.type)?.zones ?? [];
  const zoneNames = zones.map((id) => site?.zones.find((z) => z.id === id)?.name ?? id);
  const policy = site?.policies.find((p) => p.event_type === event.type);
  const [confirming, setConfirming] = useState(false);

  // a stop takes two clicks: a misclick must not cancel a live announcement
  useEffect(() => {
    if (!confirming) return;
    const t = window.setTimeout(() => setConfirming(false), 3500);
    return () => window.clearTimeout(t);
  }, [confirming]);

  const border =
    status === "pending" ? "border-alert/60" : status === "playing" ? "border-phosphor/60" : "border-ink-600";

  return (
    <li
      data-testid="event-card"
      data-status={status}
      className={`rounded-md border ${border} bg-ink-900 ${status === "playing" ? "shadow-[0_0_22px_-8px_rgba(255,176,32,0.55)]" : ""}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2 px-3 pt-2.5">
        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-dim">
            <span>#{event.id}</span>
            <span>v{event.version}</span>
            <span>{TYPE_LABELS[event.type]}</span>
            <span className="text-ink-500">|</span>
            <span>{site?.name ?? event.site_id}</span>
            <span className="text-ink-500">|</span>
            <span>{event.source}</span>
          </div>
          <h3 className="truncate text-base font-semibold capitalize text-slate-100">{headline(event)}</h3>
          <p className="text-xs capitalize text-dim">{detail(event)}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Tag tone={s.tone}>
            <Led tone={s.tone} pulse={status === "playing" || status === "pending"} />
            {s.label}
          </Tag>
          {event.approval === "pending" && event.approval_reason && (
            <span className="font-mono text-[10px] uppercase text-alert">{event.approval_reason}</span>
          )}
          {event.approved_by && event.approved_by !== "auto" && (
            <span className="font-mono text-[10px] uppercase text-dim">approved by {event.approved_by}</span>
          )}
        </div>
      </div>

      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 border-y border-ink-600 px-3 py-1.5 font-mono text-[11px] sm:grid-cols-4">
        <Cell
          k="Next play"
          v={
            status === "pending"
              ? "after approval"
              : status === "playing" && playingIn.length
                ? "on air now"
                : nextAt
                  ? countdown(nextAt, now)
                  : "none"
          }
          tone={status === "scheduled" ? "info" : status === "playing" ? "phosphor" : undefined}
        />
        <Cell k="Zones" v={zoneNames.join(", ") || "-"} />
        <Cell
          k="Repeat"
          v={policy ? `${every(policy.interval_s)}${policy.max_plays ? ` × ${policy.max_plays}` : ""}` : "-"}
        />
        <Cell k="Playing in" v={playingIn.join(", ") || "-"} />
      </dl>

      <Scripts event={event} />

      <div className="flex flex-wrap items-center gap-2 px-3 pb-2.5">
        {event.state === "active" && event.approval === "pending" && (
          <button className={BTN_ALERT} onClick={onApprove} data-testid="approve">
            Approve
          </button>
        )}
        {event.state === "active" && (
          <>
            <button className={BTN} onClick={onEdit} data-testid="edit">
              Edit
            </button>
            <button
              className={confirming ? BTN_ALERT : BTN}
              data-testid="stop"
              title={
                event.type === "line_status"
                  ? "Cancel its plays and announce the line is back to good service"
                  : "Cancel its plays now. A clip already on air finishes."
              }
              onClick={() => (confirming ? (setConfirming(false), onStop()) : setConfirming(true))}
            >
              {confirming ? "Confirm stop" : "Stop"}
            </button>
          </>
        )}
      </div>
    </li>
  );
}

const every = (seconds: number) => `every ${seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} s`}`;

function Cell({ k, v, tone }: { k: string; v: string; tone?: Tone }) {
  const color = tone === "info" ? "text-info" : tone === "phosphor" ? "text-phosphor" : "text-slate-200";
  return (
    <div className="min-w-0">
      <dt className="text-[9px] uppercase tracking-[0.16em] text-dim">{k}</dt>
      <dd className={`truncate ${color}`}>{v}</dd>
    </div>
  );
}

// --- scripts per language ---------------------------------------------------------------------

function Scripts({ event }: { event: RelayEvent }) {
  const all = event.announcements;
  const resolution = all.filter((a) => a.is_resolution);
  const regular = all.filter((a) => !a.is_resolution);
  const languages = [...new Set(regular.map((a) => a.language))];
  const levels = [...new Set(regular.map((a) => a.urgency))].sort();
  const [lang, setLang] = useState(languages[0] ?? "en");
  const [level, setLevel] = useState<number | "resolution">(levels[0] ?? 1);
  const showResolution = event.state === "resolved" && resolution.length > 0;
  const shown = showResolution ? "resolution" : level;
  const pool = shown === "resolution" ? resolution : regular.filter((a) => a.urgency === shown);
  const ann = pool.find((a) => a.language === lang) ?? pool[0];

  if (all.length === 0) return <p className="px-3 py-3 text-xs text-dim">No scripts.</p>;
  return (
    <div className="space-y-2 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {languages.map((l) => (
          <Chip key={l} on={l === lang} onClick={() => setLang(l)} title={LANGUAGE_NAMES[l]}>
            {l}
          </Chip>
        ))}
        {levels.length > 1 && (
          <>
            <span className="mx-1 text-ink-500">|</span>
            {levels.map((u) => (
              <Chip key={u} on={u === shown} onClick={() => setLevel(u)} title={`Urgency level ${u}`}>
                L{u}
              </Chip>
            ))}
          </>
        )}
        {resolution.length > 0 && (
          <Chip on={shown === "resolution"} onClick={() => setLevel("resolution")}>
            resolution
          </Chip>
        )}
      </div>
      {ann ? <Script a={ann} /> : <p className="text-xs text-dim">No script for this language.</p>}
    </div>
  );
}

function Chip({
  on,
  onClick,
  children,
  title,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className={`rounded-sm border px-1.5 py-px font-mono text-[11px] font-semibold uppercase tracking-wider ${
        on ? "border-phosphor/70 bg-phosphor/15 text-phosphor" : "border-ink-500 text-dim hover:text-slate-200"
      }`}
    >
      {children}
    </button>
  );
}

export function Script({ a }: { a: Announcement }) {
  const violations = a.rejections.flatMap((r) => r.violations.map((v) => ({ ...v, attempt: r.attempt })));
  return (
    <div className="rounded-sm border border-ink-600 bg-ink-950 p-2.5">
      <p className="text-sm leading-snug text-slate-100" lang={a.language}>
        {a.script}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Tag tone={a.source === "llm" ? "info" : "phosphor"}>
          {a.source === "llm" ? `LLM ${a.model ?? ""}` : "template"}
        </Tag>
        {violations.length === 0 ? (
          <Tag tone="ok">fact lock passed</Tag>
        ) : a.source === "template" ? (
          <Tag tone="alert">LLM draft rejected → template</Tag>
        ) : (
          <Tag tone="alert">rejected once, passed on retry</Tag>
        )}
        {a.duration_s !== null && <span className="font-mono text-[10px] text-dim">{a.duration_s.toFixed(1)} s</span>}
        <ClipButton url={a.audio_url} error={a.audio_error} />
      </div>
      {violations.length > 0 && (
        <ul className="mt-2 space-y-0.5 border-l-2 border-alert/60 pl-2 font-mono text-[11px] text-alert">
          {violations.map((v, i) => (
            <li key={i}>
              attempt {v.attempt} · {v.layer}/{v.code}: {v.detail}
              {v.span && <span className="text-red-300"> “{v.span}”</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** A small play/stop button for hearing one clip in the operator's own headphones. */
function ClipButton({ url, error }: { url: string | null; error: string | null }) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  useEffect(() => () => audio.current?.pause(), []);
  if (!url) return error ? <span className="font-mono text-[10px] text-alert">no audio: {error}</span> : null;
  const toggle = () => {
    if (!audio.current) {
      audio.current = new Audio(audioSrc(url));
      audio.current.onended = () => setPlaying(false);
    }
    if (playing) {
      audio.current.pause();
      audio.current.currentTime = 0;
      setPlaying(false);
    } else {
      audio.current.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
    }
  };
  return (
    <button className={`${BTN} ml-auto !py-0.5`} onClick={toggle} title="Listen to this clip here only">
      {playing ? "■ stop" : "▶ listen"}
    </button>
  );
}
