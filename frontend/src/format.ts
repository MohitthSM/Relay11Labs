// Small display helpers shared by the pages. No state, no fetching.
import type { EventType, RelayEvent } from "./types";

export const LANGUAGE_NAMES: Record<string, string> = { en: "English", es: "Español", fr: "Français" };

export const TYPE_LABELS: Record<EventType, string> = {
  line_status: "Line status",
  passenger_page: "Passenger page",
  stand_change: "Stand change",
};

const words = (s: unknown) => String(s ?? "").replace(/_/g, " ");

/** One-line headline for an event. Shows passenger names: the console is allowed to (R11). */
export function headline(e: Pick<RelayEvent, "type" | "payload">): string {
  const p = e.payload;
  if (e.type === "line_status") return `${p.line} line · ${words(p.status)}`;
  if (e.type === "passenger_page") return `${p.passenger_name} · ${p.flight_number} · gate ${p.gate}`;
  return `Route ${p.route} · stand ${p.from_stand} → ${p.to_stand}`;
}

/** The smaller line under the headline: reason and place, destination, and so on. */
export function detail(e: Pick<RelayEvent, "type" | "payload">): string {
  const p = e.payload;
  const bits: string[] = [];
  if (e.type === "line_status") {
    if (p.reason) bits.push(words(p.reason));
    if (p.location) bits.push(`at ${p.location}`);
    if (p.delay_minutes) bits.push(`up to ${p.delay_minutes} min`);
  } else if (e.type === "passenger_page") {
    bits.push(`to ${p.destination}`, words(p.page_type));
  } else if (p.reason) {
    bits.push(words(p.reason));
  }
  return bits.join(" · ");
}

export const clock = (d: Date) => d.toLocaleTimeString("en-GB", { hour12: false });

/** Time until an ISO time, as m:ss (or "now"). */
export function countdown(iso: string, nowMs: number): string {
  const s = Math.round((new Date(iso).getTime() - nowMs) / 1000);
  if (s <= 0) return "now";
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export const money = (usd: number) => (usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(3)}`);
export const percent = (x: number) => `${Math.round(x * 100)}%`;
