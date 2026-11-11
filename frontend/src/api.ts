// All backend calls live here. The dev server proxies /api to the backend.
import type {
  Health,
  Metrics,
  MetricRow,
  NoteDraftOut,
  Preview,
  RelayEvent,
  RunStatus,
  ScenarioList,
  Site,
  StateMessage,
  Zone,
} from "./types";

const BASE = "/api";

/** A non-2xx answer. `fields` maps a payload field to its validation message (422). */
export class ApiError extends Error {
  status: number;
  fields: Record<string, string>;
  constructor(status: number, message: string, fields: Record<string, string> = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) throw await toError(res);
  return res.json() as Promise<T>;
}

async function toError(res: Response): Promise<ApiError> {
  const text = await res.text();
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (typeof detail === "string") return new ApiError(res.status, detail);
    if (Array.isArray(detail)) {
      const fields: Record<string, string> = {};
      for (const d of detail as { loc?: unknown[]; msg?: string }[]) {
        const name = String(d.loc?.[d.loc.length - 1] ?? "");
        fields[name] = d.msg ?? "invalid";
      }
      const summary = Object.entries(fields).map(([k, v]) => `${k}: ${v}`).join("; ");
      return new ApiError(res.status, summary || `${res.status}`, fields);
    }
  } catch {
    // not JSON: fall through to the raw text
  }
  return new ApiError(res.status, `${res.status} ${text}`);
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const getHealth = () => request<Health>("/health");
export const getSites = () => request<Site[]>("/sites");
export const listEvents = (state: string, detail = true, limit = 100) =>
  request<RelayEvent[]>(`/events?state=${state}&detail=${detail}&limit=${limit}`);
export const getEvent = (id: number) => request<RelayEvent>(`/events/${id}`);
export const resolveEvent = (id: number) => request<RelayEvent>(`/events/${id}/resolve`, send("POST"));
export const approveEvent = (id: number, operator: string) =>
  request<RelayEvent>(`/events/${id}/approve`, send("POST", { operator }));
export const previewEvent = (siteId: string, payload: Record<string, unknown>, source: string) =>
  request<Preview>("/events/preview", send("POST", { site_id: siteId, payload, source }));
export const createEvent = (
  siteId: string,
  payload: Record<string, unknown>,
  source: "form" | "staff_note",
  staffNote?: string,
) => request<RelayEvent>("/events", send("POST", { site_id: siteId, payload, source, staff_note: staffNote }));
export const updateEvent = (eventKey: string, payload: Record<string, unknown>) =>
  request<RelayEvent>(`/events/${encodeURIComponent(eventKey)}`, send("PUT", { payload }));
export const draftFromNote = (siteId: string, note: string) =>
  request<NoteDraftOut>("/events/from-note", send("POST", { site_id: siteId, note }));

export const listScenarios = () => request<ScenarioList>("/scenarios");
export const runScenario = (name: string, speed: number, autoApprove: boolean) =>
  request<RunStatus>(`/scenarios/${name}/run?speed=${speed}&auto_approve=${autoApprove}`, send("POST"));
export const getRun = (runId: string) => request<RunStatus>(`/scenarios/runs/${runId}`);
export const setSpeed = (scale: number) => request<{ scale: number }>("/speed", send("PUT", { scale }));
export const setHallucinate = (enabled: boolean) =>
  request<{ hallucinate: boolean }>("/demo/hallucinate", send("PUT", { enabled }));

export const listZones = () => request<Zone[]>("/playback/zones");
export const getMetrics = () => request<Metrics>("/metrics/summary");
export const getMetricRows = () => request<MetricRow[]>("/metrics/announcements?limit=60");
export const audioSrc = (url: string) => BASE + url;

export type SocketStatus = "connecting" | "open" | "closed";

/** The one real-time channel: state changes and play commands (D8). It reconnects by itself
 * (1 s, then up to 5 s apart) and tells the caller each time the status changes, so a page can
 * re-fetch what it missed. Returns a function that closes it for good. */
export function watchEvents(
  onMessage: (m: StateMessage) => void,
  onStatus: (s: SocketStatus) => void,
): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let ws: WebSocket | null = null;
  let retry: number | undefined;
  let stopped = false;
  let delay = 1000;

  const connect = () => {
    onStatus("connecting");
    ws = new WebSocket(`${proto}://${location.host}${BASE}/ws/events`);
    ws.onopen = () => {
      delay = 1000;
      onStatus("open");
    };
    ws.onmessage = (e) => onMessage(JSON.parse(e.data) as StateMessage);
    ws.onclose = () => {
      if (stopped) return; // a socket we closed ourselves must not overwrite a newer one's status
      onStatus("closed");
      retry = window.setTimeout(connect, delay);
      delay = Math.min(delay * 2, 5000);
    };
  };
  connect();
  return () => {
    stopped = true;
    window.clearTimeout(retry);
    // closing a socket that is still connecting makes the browser log an error: close it once open
    if (ws?.readyState === WebSocket.CONNECTING) ws.onopen = () => ws?.close();
    else ws?.close();
  };
}
