import { LANGUAGE_NAMES } from "../format";
import type { Preview, ScriptSlot } from "../types";
import { Tag } from "./ui";

// What the engine wrote for each language and urgency level, and what the fact lock said about
// it. Shown before anything is broadcast. Nothing here has been created or played.
export default function ScriptPreview({ preview }: { preview: Preview }) {
  const levels = [...new Set(preview.scripts.map((s) => s.urgency))].sort();
  const rejected = preview.scripts.filter((s) => s.rejections.length > 0).length;
  return (
    <div data-testid="preview" className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {preview.action === "create" && <Tag tone="info">new event</Tag>}
        {preview.action === "update" && <Tag tone="phosphor">update of #{preview.active_event_id}: replaces it</Tag>}
        {preview.action === "duplicate" && (
          <Tag tone="alert">identical to #{preview.active_event_id}: nothing would change</Tag>
        )}
        {preview.needs_approval ? (
          <Tag tone="alert">waits for approval · {preview.needs_approval}</Tag>
        ) : (
          <Tag tone="ok">plays at once</Tag>
        )}
        {rejected === 0 ? (
          <Tag tone="ok">fact lock: all {preview.scripts.length} scripts passed</Tag>
        ) : (
          <Tag tone="alert">
            fact lock rejected {rejected} of {preview.scripts.length} drafts
          </Tag>
        )}
      </div>
      {levels.map((level) => (
        <div key={level}>
          {levels.length > 1 && (
            <h4 className="mb-1 font-mono text-[10px] uppercase tracking-[0.18em] text-dim">
              Repeat {level}
              {level === 1 ? " · first play" : level === levels.length ? " · last call" : " · firmer"}
            </h4>
          )}
          <ul className="space-y-1.5">
            {preview.scripts
              .filter((s) => s.urgency === level)
              .map((s) => (
                <Slot key={`${s.language}-${s.urgency}`} slot={s} />
              ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function Slot({ slot: s }: { slot: ScriptSlot }) {
  const violations = s.rejections.flatMap((r) => r.violations.map((v) => ({ ...v, attempt: r.attempt })));
  return (
    <li className={`rounded-sm border p-2 ${violations.length ? "border-alert/50 bg-alert/5" : "border-ink-600 bg-ink-950"}`}>
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[11px] font-bold uppercase text-phosphor" title={LANGUAGE_NAMES[s.language]}>
          {s.language}
        </span>
        <Tag tone={s.source === "llm" ? "info" : "phosphor"}>{s.source === "llm" ? `LLM ${s.model ?? ""}` : "template"}</Tag>
        {violations.length === 0 ? (
          <Tag tone="ok">fact check passed</Tag>
        ) : s.source === "template" ? (
          <Tag tone="alert">draft rejected · template used</Tag>
        ) : (
          <Tag tone="alert">rejected once · passed on retry</Tag>
        )}
      </div>
      <p className="text-sm leading-snug text-slate-100" lang={s.language}>
        {s.text}
      </p>
      {violations.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 border-l-2 border-alert/60 pl-2 font-mono text-[11px] text-alert">
          {violations.map((v, i) => (
            <li key={i}>
              attempt {v.attempt} · {v.layer}/{v.code}: {v.detail}
              {v.span && <span className="text-red-300"> “{v.span}”</span>}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
