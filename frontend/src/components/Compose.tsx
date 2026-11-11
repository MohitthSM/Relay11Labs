import { useEffect, useState } from "react";
import { ApiError, createEvent, previewEvent, updateEvent } from "../api";
import { TYPE_LABELS } from "../format";
import type { EventType, NoteDraft, Preview, RelayEvent, Site } from "../types";
import EventForm, { buildPayload, emptyValues, missingRequired, valuesFromPayload } from "./EventForm";
import type { Values } from "./EventForm";
import ScriptPreview from "./ScriptPreview";
import StaffNote from "./StaffNote";
import { BTN, BTN_PRIMARY, INPUT, LABEL, Panel } from "./ui";

type Props = {
  sites: Site[];
  edit: RelayEvent | null; // set: a new version of this event
  onClose: () => void;
  onDone: (event: RelayEvent) => void;
};

// The "new announcement" panel: a staff note box and a structured form, then the generated
// scripts with their fact-check result, then Broadcast. Nothing is created until Broadcast, and an
// edit to any field throws the preview away, so what is broadcast is always what was checked.
export default function Compose({ sites, edit, onClose, onDone }: Props) {
  const [siteId, setSiteId] = useState(edit?.site_id ?? sites[0]?.id ?? "");
  const site = sites.find((s) => s.id === siteId);
  const types = site?.policies.map((p) => p.event_type) ?? [];
  const [type, setType] = useState<EventType>(edit?.type ?? types[0] ?? "line_status");
  const [values, setValues] = useState<Values>(edit ? valuesFromPayload(edit.payload) : emptyValues(type));
  const [note, setNote] = useState("");
  const [fromNote, setFromNote] = useState(false); // the fields came from a staff note
  const [hints, setHints] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<"preview" | "broadcast" | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const changeSite = (id: string) => {
    const next = sites.find((s) => s.id === id);
    const t = next?.policies[0]?.event_type ?? "line_status";
    setSiteId(id);
    setType(t);
    setValues(emptyValues(t));
    reset();
  };
  const changeType = (t: EventType) => {
    setType(t);
    setValues(emptyValues(t));
    reset();
  };
  const reset = () => {
    setPreview(null);
    setErrors({});
    setMessage(null);
    setHints({});
    setFromNote(false);
  };
  const change = (name: string, value: string) => {
    setValues((v) => ({ ...v, [name]: value }));
    setPreview(null); // what was checked is no longer what is typed
    setErrors((e) => ({ ...e, [name]: "" }));
    setMessage(null);
  };

  const source = fromNote ? "staff_note" : "form";
  const missing = missingRequired(type, values);

  const check = async (payload = buildPayload(type, values), src = source) => {
    setBusy("preview");
    setMessage(null);
    setErrors({});
    try {
      setPreview(await previewEvent(siteId, payload, src));
    } catch (e) {
      setPreview(null);
      const err = e as ApiError;
      setErrors(err.fields ?? {});
      setMessage(err.message);
    } finally {
      setBusy(null);
    }
  };

  const gotDraft = (draft: NoteDraft) => {
    if (draft.event_type && draft.event_type !== type && types.includes(draft.event_type)) setType(draft.event_type);
    const t = draft.event_type ?? type;
    setValues({ ...emptyValues(t), ...valuesFromPayload({ ...draft.fields, ...(draft.payload ?? {}) }) });
    setFromNote(true);
    setHints({
      ...Object.fromEntries(Object.entries(draft.dropped).map(([k, v]) => [k, v])),
      ...Object.fromEntries(draft.missing.map((k) => [k, "not in the note: please fill in"])),
    });
    setErrors({});
    setPreview(null);
    if (draft.status === "ready" && draft.payload) void check(draft.payload, "staff_note");
  };

  const broadcast = async () => {
    if (!preview) return;
    setBusy("broadcast");
    setMessage(null);
    const payload = buildPayload(type, values);
    try {
      let event: RelayEvent;
      if (edit) event = await updateEvent(edit.event_key, payload);
      else if (preview.action === "update") event = await updateEvent(preview.event_key, payload);
      else event = await createEvent(siteId, payload, source, fromNote ? note : undefined);
      onDone(event);
    } catch (e) {
      const err = e as ApiError;
      setErrors(err.fields ?? {});
      setMessage(err.message);
      setBusy(null);
    }
  };

  if (!site) return null;
  const label = edit ? `Update event #${edit.id}` : preview?.action === "update" ? "Broadcast update" : "Broadcast";

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/60" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside
        data-testid="compose"
        className="flex h-full w-full max-w-[640px] animate-slidein flex-col border-l border-ink-500 bg-ink-900 shadow-2xl"
      >
        <header className="flex items-center justify-between border-b border-ink-600 px-4 py-2.5">
          <h2 className="font-mono text-xs font-bold uppercase tracking-[0.2em] text-phosphor">
            {edit ? `Edit event #${edit.id}` : "New announcement"}
          </h2>
          <button className={BTN} onClick={onClose} aria-label="Close">
            Esc ✕
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {!edit && (
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="site" className={LABEL}>
                  Site
                </label>
                <select id="site" className={INPUT} value={siteId} onChange={(e) => changeSite(e.target.value)}>
                  {sites.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </div>
              {types.length > 1 && (
                <div>
                  <label htmlFor="type" className={LABEL}>
                    Event type
                  </label>
                  <select id="type" className={INPUT} value={type} onChange={(e) => changeType(e.target.value as EventType)}>
                    {types.map((t) => (
                      <option key={t} value={t}>
                        {TYPE_LABELS[t]}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          )}

          {!edit && (
            <StaffNote siteId={siteId} note={note} onNote={setNote} onDraft={gotDraft} disabled={busy !== null} />
          )}

          <Panel title={`${TYPE_LABELS[type]}${edit ? ` · version ${edit.version + 1}` : ""}`}>
            <EventForm type={type} site={site} values={values} onChange={change} errors={errors} hints={hints} editing={Boolean(edit)} />
          </Panel>

          {message && (
            <p role="alert" className="rounded-sm border border-alert/50 bg-alert/10 p-2 font-mono text-xs text-alert">
              {message}
            </p>
          )}

          {preview && <ScriptPreview preview={preview} />}
        </div>

        <footer className="flex items-center justify-between gap-2 border-t border-ink-600 bg-ink-800 px-4 py-3">
          <span className="font-mono text-[10px] uppercase tracking-wider text-dim">
            {missing.length > 0 ? `needs: ${missing.join(", ")}` : preview ? "checked · ready" : "check the script first"}
          </span>
          <span className="flex gap-2">
            <button
              className={preview ? BTN : BTN_PRIMARY}
              disabled={busy !== null || missing.length > 0}
              onClick={() => void check()}
              data-testid="check"
            >
              {busy === "preview" ? "Checking…" : preview ? "Re-check" : "Generate script & fact-check"}
            </button>
            <button
              className={BTN_PRIMARY}
              disabled={busy !== null || !preview || preview.action === "duplicate"}
              onClick={() => void broadcast()}
              data-testid="broadcast"
              title={preview ? "" : "Generate and check the script first"}
            >
              {busy === "broadcast" ? "Sending…" : label}
            </button>
          </span>
        </footer>
      </aside>
    </div>
  );
}
