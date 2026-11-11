import type { EventType, Site } from "../types";
import { INPUT, LABEL } from "./ui";

// One form per event type. The field list mirrors the payload models in backend/relay/schemas.py
// (by hand, like types.ts). The server validates for real: a 422 comes back per field.

export type Values = Record<string, string>;

type FieldSpec = {
  name: string;
  label: string;
  kind: "text" | "select" | "number";
  options?: [value: string, label: string][];
  suggest?: "lines" | "stations" | "cities"; // gazetteer names offered as suggestions
  required?: boolean;
  upper?: boolean; // codes are typed in capitals
  identity?: boolean; // names the thing the event is about: fixed when editing
  placeholder?: string;
};

const REASONS: [string, string][] = [
  ["", "no cause given"],
  ["signal_failure", "signal failure"],
  ["train_fault", "train fault"],
  ["customer_incident", "customer incident"],
  ["power_failure", "power failure"],
  ["staff_shortage", "staff shortage"],
  ["engineering_works", "engineering works"],
  ["roadworks", "roadworks"],
  ["operational_reasons", "operational reasons"],
];

const FIELDS: Record<EventType, FieldSpec[]> = {
  line_status: [
    { name: "line", label: "Line", kind: "text", suggest: "lines", required: true, identity: true, placeholder: "Jubilee" },
    {
      name: "status",
      label: "Status",
      kind: "select",
      required: true,
      options: [
        ["severe_delays", "severe delays"],
        ["minor_delays", "minor delays"],
        ["part_suspended", "part suspended"],
        ["suspended", "suspended"],
        ["good_service", "good service"],
      ],
    },
    { name: "reason", label: "Cause", kind: "select", options: REASONS },
    { name: "location", label: "Location", kind: "text", suggest: "stations", placeholder: "Canary Wharf" },
    { name: "delay_minutes", label: "Delay (minutes)", kind: "number", placeholder: "optional" },
  ],
  passenger_page: [
    { name: "passenger_name", label: "Passenger name", kind: "text", required: true, identity: true, placeholder: "John Smith" },
    { name: "flight_number", label: "Flight", kind: "text", required: true, upper: true, identity: true, placeholder: "BA117" },
    { name: "destination", label: "Destination", kind: "text", suggest: "cities", required: true, placeholder: "New York" },
    { name: "gate", label: "Gate", kind: "text", required: true, upper: true, placeholder: "23" },
  ],
  stand_change: [
    { name: "route", label: "Route", kind: "text", required: true, upper: true, identity: true, placeholder: "25" },
    { name: "from_stand", label: "From stand", kind: "text", required: true, upper: true, placeholder: "B" },
    { name: "to_stand", label: "To stand", kind: "text", required: true, upper: true, placeholder: "F" },
    { name: "reason", label: "Cause", kind: "select", options: REASONS },
  ],
};

const DEFAULTS: Record<EventType, Values> = {
  line_status: { status: "severe_delays", reason: "" },
  passenger_page: {},
  stand_change: { reason: "" },
};

export const emptyValues = (type: EventType): Values => ({ ...DEFAULTS[type] });

export function valuesFromPayload(payload: Record<string, unknown>): Values {
  const out: Values = {};
  for (const [k, v] of Object.entries(payload)) if (k !== "type" && v != null) out[k] = String(v);
  return out;
}

/** The payload the API expects: only fields with a value, numbers as numbers. */
export function buildPayload(type: EventType, values: Values): Record<string, unknown> {
  const payload: Record<string, unknown> = { type };
  for (const f of FIELDS[type]) {
    const raw = (values[f.name] ?? "").trim();
    if (raw === "") continue;
    payload[f.name] = f.kind === "number" ? Number(raw) : raw;
  }
  return payload;
}

export function missingRequired(type: EventType, values: Values): string[] {
  return FIELDS[type].filter((f) => f.required && !(values[f.name] ?? "").trim()).map((f) => f.label);
}

type Props = {
  type: EventType;
  site: Site;
  values: Values;
  onChange: (name: string, value: string) => void;
  errors: Record<string, string>; // from the server (422)
  hints: Record<string, string>; // from a staff note: "not found in note", "missing"
  editing: boolean;
};

export default function EventForm({ type, site, values, onChange, errors, hints, editing }: Props) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {FIELDS[type].map((f) => {
        const id = `f-${f.name}`;
        const err = errors[f.name];
        const hint = hints[f.name];
        const common = {
          id,
          name: f.name,
          disabled: editing && f.identity,
          className: `${INPUT} ${err ? "!border-alert" : hint ? "!border-phosphor/70" : ""}`,
          value: values[f.name] ?? "",
        };
        const list = f.suggest ? site[f.suggest] : undefined;
        return (
          <div key={f.name} className={f.name === "location" || f.name === "passenger_name" ? "sm:col-span-2" : ""}>
            <label htmlFor={id} className={LABEL}>
              {f.label}
              {f.required && <span className="text-phosphor"> *</span>}
            </label>
            {f.kind === "select" ? (
              <select {...common} onChange={(e) => onChange(f.name, e.target.value)}>
                {f.options!.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            ) : (
              <>
                <input
                  {...common}
                  type={f.kind === "number" ? "number" : "text"}
                  min={f.kind === "number" ? 1 : undefined}
                  max={f.kind === "number" ? 180 : undefined}
                  placeholder={f.placeholder}
                  list={list ? `${id}-list` : undefined}
                  autoComplete="off"
                  onChange={(e) => onChange(f.name, f.upper ? e.target.value.toUpperCase() : e.target.value)}
                />
                {list && (
                  <datalist id={`${id}-list`}>
                    {list.map((n) => (
                      <option key={n} value={n} />
                    ))}
                  </datalist>
                )}
              </>
            )}
            {err && <p className="mt-1 font-mono text-[10px] text-alert">{err}</p>}
            {!err && hint && <p className="mt-1 font-mono text-[10px] text-phosphor">{hint}</p>}
          </div>
        );
      })}
    </div>
  );
}
