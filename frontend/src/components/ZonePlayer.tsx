import { useEffect, useRef, useState } from "react";
import { audioSrc } from "../api";
import type { PlayCommand, Zone } from "../types";

// The server owns the zone clock (D9) and sends one clip at a time. At high demo speeds clips
// arrive faster than they can play, so the player never cuts a clip but keeps only the newest few.
const MAX_QUEUE = 3;

type Props = {
  zone: Zone;
  audioOn: boolean;
  subscribe: (zoneId: string, onPlay: (cmd: PlayCommand) => void) => () => void;
};

const time = (iso: string) => new Date(iso).toLocaleTimeString();

export default function ZonePlayer({ zone, audioOn, subscribe }: Props) {
  const audio = useRef<HTMLAudioElement>(null);
  const queue = useRef<PlayCommand[]>([]);
  const busy = useRef(false);
  const [heard, setHeard] = useState<PlayCommand | null>(null);

  useEffect(() => {
    const playNext = () => {
      const next = queue.current.shift();
      busy.current = Boolean(next);
      setHeard(next ?? null);
      if (next && audio.current) {
        audio.current.src = audioSrc(next.url);
        audio.current.play().catch(() => playNext()); // blocked or broken clip: move on
      }
    };
    const el = audio.current;
    el?.addEventListener("ended", playNext);
    const unsubscribe = subscribe(zone.zone_id, (cmd) => {
      if (!audioOn) return;
      queue.current.push(cmd);
      queue.current.splice(0, Math.max(0, queue.current.length - MAX_QUEUE));
      if (!busy.current) playNext();
    });
    return () => {
      el?.removeEventListener("ended", playNext);
      unsubscribe();
    };
  }, [zone.zone_id, audioOn, subscribe]);

  const onAir = zone.on_air;
  return (
    <li className="rounded border bg-white p-3 text-sm">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-medium">{zone.name}</span>
        <span className="font-mono text-xs text-slate-500">{zone.languages.join(" · ")}</span>
      </div>
      {onAir ? (
        <div className="rounded bg-green-50 p-2">
          <div className="text-xs text-green-800">
            on air: event #{onAir.event_id} · {onAir.language.toUpperCase()} · priority {onAir.priority}
          </div>
          <p>{onAir.caption}</p>
        </div>
      ) : (
        <p className="text-slate-400">silent</p>
      )}
      {zone.queue.length > 0 && (
        <ul className="mt-1 text-xs text-slate-600">
          {zone.queue.map((q, i) => (
            <li key={i}>
              waiting: event #{q.event_id} · priority {q.priority} · {q.clips_left} clip(s)
              {q.interrupted && " · interrupted, resumes next"}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-1 flex justify-between text-xs text-slate-500">
        <span>{zone.next ? `next: event #${zone.next.event_id} at ${time(zone.next.at)}` : "nothing scheduled"}</span>
        {zone.held > 0 && <span className="text-red-700">{zone.held} awaiting approval</span>}
      </div>
      {audioOn && heard && <div className="mt-1 text-xs text-blue-700">speaker: event #{heard.event_id} {heard.language}</div>}
      <audio ref={audio} className="hidden" />
    </li>
  );
}
