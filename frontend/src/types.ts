// Hand-written mirror of the backend DTOs (api/events.py, api/scenarios.py, main.py /health).

export type Health = {
  status: string;
  llm: "live" | "mock";
  tts: "live" | "mock";
  llm_requested: string;
  tts_requested: string;
};

export type Announcement = {
  id: number;
  language: string;
  urgency: number;
  is_resolution: boolean;
  script: string;
  spoken_text: string;
  source: "llm" | "template";
  model: string | null;
  rejections: { attempt: number; model: string; violations: { layer: string; code: string; detail: string }[] }[];
  clip_id: string | null;
  audio_url: string | null;
  duration_s: number | null;
  audio_error: string | null;
};

export type EventState = "active" | "superseded" | "resolved";

export type RelayEvent = {
  id: number;
  event_key: string;
  site_id: string;
  type: "line_status" | "passenger_page" | "stand_change";
  version: number;
  state: EventState;
  priority: number;
  source: string;
  payload: Record<string, unknown>;
  created_at: string;
  resolved_at: string | null;
  announcements: Announcement[];
};

export type ScenarioInfo = {
  name: string;
  description: string;
  site_id: string;
  steps: number;
  duration_s: number;
};

export type ScenarioList = { speed: number; scenarios: ScenarioInfo[] };

export type RunStatus = {
  run_id: string;
  scenario: string;
  state: "running" | "done" | "failed";
  steps_done: number;
  steps_total: number;
  error: string | null;
};

// WebSocket /ws/events
export type StateMessage =
  | { type: "snapshot"; active: { event_id: number; site_id: string; event_type: string; version: number }[] }
  | { type: "event.created" | "event.updated" | "event.resolved"; event_id: number; site_id: string; event_type: string; version: number; state: EventState };
