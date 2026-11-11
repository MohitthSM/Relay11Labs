// Hand-written mirror of the backend DTOs (api/events.py, api/playback.py, api/scenarios.py,
// api/ws.py, main.py /health).

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
  approval: Approval;
  approval_reason: string | null;
  approved_by: string | null;
  approved_at: string | null;
  duplicate: boolean;
  announcements: Announcement[];
};

export type Approval = "auto" | "pending" | "approved";

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

// GET /playback/zones
export type Zone = {
  site_id: string;
  zone_id: string;
  name: string;
  languages: string[];
  on_air: {
    event_id: number;
    play_id: number | null;
    announcement_id: number;
    language: string;
    priority: number;
    until: string;
    caption: string | null;
  } | null;
  queue: { event_id: number; priority: number; clips_left: number; interrupted: boolean }[];
  next: { event_id: number; at: string; is_resolution: boolean } | null;
  held: number;
};

// WebSocket /ws/events: the one real-time channel (state changes and play commands, D8)
export type PlayCommand = {
  type: "play";
  zone_id: string;
  event_id: number;
  play_id: number;
  announcement_id: number;
  language: string;
  url: string;
  duration_s: number;
  priority: number;
  is_resolution: boolean;
  resumed: boolean;
};

export type StateMessage =
  | { type: "snapshot"; active: { event_id: number; site_id: string; event_type: string; version: number }[] }
  | {
      type: "event.created" | "event.updated" | "event.resolved" | "event.approved";
      event_id: number;
      site_id: string;
      event_type: string;
      version: number;
      state: EventState;
      approval: Approval;
    }
  | PlayCommand;
