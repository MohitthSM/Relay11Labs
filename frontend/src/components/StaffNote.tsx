import { useState } from "react";
import { draftFromNote } from "../api";
import type { NoteDraft } from "../types";
import { BTN, INPUT, LABEL, Tag } from "./ui";

type Props = {
  siteId: string;
  note: string;
  onNote: (note: string) => void;
  onDraft: (draft: NoteDraft) => void;
  disabled: boolean;
};

// A free-text note from the gate agent or operator. The server extracts fields from it, keeps only
// values that really appear in the note, and says what it could not find (the extraction lock).
// Nothing is created here: the fields land in the form below for the agent to check.
export default function StaffNote({ siteId, note, onNote, onDraft, disabled }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<NoteDraft | null>(null);

  const extract = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await draftFromNote(siteId, note);
      setDraft(out.draft);
      onDraft(out.draft);
    } catch (e) {
      setDraft(null);
      setError((e as Error).message); // a refused note (out of scope, instruction-like) is a 422
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <label htmlFor="staff-note" className={LABEL}>
        Staff note <span className="text-dim normal-case tracking-normal">(optional: type it, we fill the form)</span>
      </label>
      <textarea
        id="staff-note"
        className={`${INPUT} h-16 resize-none`}
        placeholder="final call Mr John Smith BA117 NYC gate 23"
        value={note}
        disabled={disabled}
        onChange={(e) => onNote(e.target.value)}
      />
      <div className="mt-1.5 flex items-center gap-2">
        <button className={BTN} disabled={busy || disabled || note.trim() === ""} onClick={extract}>
          {busy ? "Reading…" : "Extract fields"}
        </button>
        {draft && (
          <Tag tone={draft.status === "ready" ? "ok" : "phosphor"}>
            {draft.status === "ready" ? "all fields found" : "needs your input"} · via {draft.source}
          </Tag>
        )}
      </div>
      {error && <p className="mt-1.5 font-mono text-[11px] text-alert">{error}</p>}
      {draft && (draft.missing.length > 0 || Object.keys(draft.dropped).length > 0) && (
        <ul className="mt-1.5 space-y-0.5 font-mono text-[11px] text-phosphor">
          {Object.entries(draft.dropped).map(([name, why]) => (
            <li key={name}>
              {name}: {why}
            </li>
          ))}
          {draft.missing.map((name) => (
            <li key={name}>{name}: not in the note, please fill in</li>
          ))}
        </ul>
      )}
      <p className="mt-1.5 text-[11px] text-dim">
        A broadcast from a note waits for an operator to approve it before it plays.
      </p>
    </div>
  );
}
