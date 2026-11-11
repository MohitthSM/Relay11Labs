// All backend calls live here. The dev server proxies /api to the backend.
import type { Health, RelayEvent, RunStatus, ScenarioList, StateMessage } from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const getHealth = () => request<Health>("/health");
export const listEvents = () => request<RelayEvent[]>("/events");
export const getEvent = (id: number) => request<RelayEvent>(`/events/${id}`);
export const resolveEvent = (id: number) => request<RelayEvent>(`/events/${id}/resolve`, post());
export const listScenarios = () => request<ScenarioList>("/scenarios");
export const runScenario = (name: string, speed: number) =>
  request<RunStatus>(`/scenarios/${name}/run?speed=${speed}`, post());
export const audioSrc = (url: string) => BASE + url;

/** Subscribe to state changes. Returns a function that closes the socket. */
export function watchEvents(onMessage: (m: StateMessage) => void): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}${BASE}/ws/events`);
  ws.onmessage = (e) => onMessage(JSON.parse(e.data) as StateMessage);
  return () => ws.close();
}
