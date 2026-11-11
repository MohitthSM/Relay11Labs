import { audioSrc } from "../api";
import type { Announcement } from "../types";

export default function AnnouncementCard({ a }: { a: Announcement }) {
  const rejected = a.rejections.flatMap((r) => r.violations);
  return (
    <li className="rounded border bg-white p-2 text-sm">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono font-semibold uppercase">{a.language}</span>
        <span>level {a.urgency}</span>
        {a.is_resolution && <span className="rounded bg-green-100 px-1">resolution</span>}
        <span className={`rounded px-1 ${a.source === "llm" ? "bg-blue-100" : "bg-amber-100"}`}>
          {a.source === "llm" ? `LLM ${a.model ?? ""}` : "template"}
        </span>
        {a.duration_s !== null && <span className="text-slate-500">{a.duration_s.toFixed(1)} s</span>}
      </div>
      <p>{a.script}</p>
      {rejected.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-xs text-red-700">
          {rejected.map((v, i) => (
            <li key={i}>
              {v.layer}/{v.code}: {v.detail}
            </li>
          ))}
        </ul>
      )}
      {a.audio_url ? (
        <audio className="mt-1 h-8 w-full" controls preload="none" src={audioSrc(a.audio_url)} />
      ) : (
        a.audio_error && <p className="mt-1 text-xs text-red-700">no audio: {a.audio_error}</p>
      )}
    </li>
  );
}
