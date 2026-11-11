// Hand-written mirror of the backend DTOs (api/events.py, api/playback.py, api/scenarios.py,
// api/ws.py, main.py /health).

export type Health = {
  status: string;
  llm: "live" | "mock";
  tts: "live" | "mock";
  llm_requested: string;
  tts_requested: string;
  hallucinate: boolean | null; // null when the LLM is live: the toggle exists for the mock only
};

export type EventType = "line_status" | "passenger_page" | "stand_change";

// GET /sites
export type Site = {
  id: string;
  name: string;
  hub_type: string;
  languages: string[];
  zones: { id: string; name: string; languages: string[] }[];
  policies: {
    event_type: EventType;
    zones: string[];
    interval_s: number;
    max_plays: number | null;
    duration_s: number | null;
    escalate: boolean;
    priority: string;
    approval: string;
  }[];
  lines: string[];
  stations: string[];
  cities: string[];
};

export type Violation = { layer: string; code: string; detail: string; span: string };
export type Rejection = { attempt: number; model: string; violations: Violation[] };

// One script slot as the engine returns it (POST /events/preview)
export type ScriptSlot = {
  language: string;
  urgency: number;
  draft: string;
  text: string;
  spoken_text: string;
  source: "llm" | "template";
  model: string | null;
  facts_used: string[];
  rejections: Rejection[];
};

export type Preview = {
  event_key: string;
  action: "create" | "update" | "duplicate";
  active_event_id: number | null;
  needs_approval: string | null;
  scripts: ScriptSlot[];
};

// POST /events/from-note
export type NoteDraft = {
  status: "ready" | "incomplete" | "rejected";
  detail: string;
  event_type: EventType | null;
  payload: Record<string, unknown> | null;
  fields: Record<string, unknown>;
  dropped: Record<string, string>;
  missing: string[];
  source: "llm" | "rules" | "none";
};
export type NoteDraftOut = { draft: NoteDraft; preview: ScriptSlot | null };

// GET /metrics/summary and /metrics/announcements
export type Metrics = {
  events: number;
  active_events: number;
  plays: number;
  announcements: {
    total: number;
    llm: number;
    template: number;
    rejected: number;
    rejection_rate: number;
    template_rate: number;
    by_layer: Record<string, number>;
  };
  llm_routes: {
    model: string;
    role: "default" | "escalation" | "other";
    calls: number;
    accepted: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }[];
  llm_calls: number;
  llm_escalation_share: number;
  llm_cost_usd: number;
  tts_renders: number;
  tts_cache_hit_rate: number;
  tts_segment_hit_rate: number;
  tts_characters_billed: number;
  tts_characters_saved: number;
  tts_cost_usd: number;
  tts_cost_saved_usd: number;
  tts_models: { model: string; role: "live" | "scheduled" | "other"; renders: number; characters_billed: number; cost_usd: number }[];
  total_cost_usd: number;
  time_to_first_play: { n: number; p50_s: number | null; p95_s: number | null };
  avg_tts_ttfa_ms: number;
};

export type MetricRow = {
  id: number;
  event_id: number;
  event_type: EventType;
  site_id: string;
  version: number;
  language: string;
  urgency: number;
  is_resolution: boolean;
  source: "llm" | "template";
  model: string | null;
  rejection_layers: string[];
  tts_model: string | null;
  cache_hit: boolean | null;
  characters_billed: number | null;
  characters_saved: number | null;
  tts_cost_usd: number | null;
  ttfa_ms: number | null;
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
  rejections: Rejection[];
  clip_id: string | null;
  audio_url: string | null;
  duration_s: number | null;
  audio_error: string | null;
};

// withdrawn: stopped with no resolution message (TfL events when the source switches to demo)
export type EventState = "active" | "superseded" | "resolved" | "withdrawn";

export type RelayEvent = {
  id: number;
  event_key: string;
  site_id: string;
  type: EventType;
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
  state: "running" | "done" | "failed" | "stopped"; // stopped: the source switched to live
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

// GET /source and the {type: "source"} WebSocket message (Phase 7)
export type DataMode = "demo" | "live";
export type SourceStatus = {
  mode: DataMode;
  status: "off" | "waiting" | "ok" | "error" | "stale";
  warning: string | null;
  lines: { id: string; name: string; status: string | null; severity: number }[];
  line_errors: Record<string, string>;
  last_ok_at: string | null;
  failures: number;
  poll_s: number;
  app_key: boolean;
  flights: "mock" | "live";
  withdrawn?: number; // PUT /source only: TfL events withdrawn by a switch to demo
};

export type StateMessage =
  | ({ type: "source" } & Omit<SourceStatus, "flights">)
  | { type: "snapshot"; active: { event_id: number; site_id: string; event_type: string; version: number }[] }
  | {
      // event.audio: the later urgency levels have been voiced in the background
      type: "event.created" | "event.updated" | "event.resolved" | "event.approved" | "event.withdrawn" | "event.audio";
      event_id: number;
      site_id: string;
      event_type: string;
      version: number;
      state: EventState;
      approval: Approval;
    }
  | PlayCommand;
