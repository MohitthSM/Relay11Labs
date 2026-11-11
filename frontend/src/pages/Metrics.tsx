import { useCallback, useEffect, useState } from "react";
import { getMetricRows, getMetrics } from "../api";
import { Panel, Tag } from "../components/ui";
import type { Tone } from "../components/ui";
import { money, percent } from "../format";
import type { Live } from "../live";
import type { MetricRow, Metrics as MetricsData } from "../types";

// Everything here comes from database rows (LLM calls, voice renders, plays), never passenger
// names. It refreshes when events change and every few seconds while open.
export default function Metrics({ live }: { live: Live }) {
  const [m, setM] = useState<MetricsData | null>(null);
  const [rows, setRows] = useState<MetricRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([getMetrics(), getMetricRows()])
      .then(([summary, table]) => {
        setM(summary);
        setRows(table);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 3000);
    const unsubscribe = live.subscribe((msg) => msg.type.startsWith("event.") && load());
    return () => {
      window.clearInterval(timer);
      unsubscribe();
    };
  }, [live, load]);

  if (error) return <p className="rounded-sm border border-alert/50 bg-alert/10 p-2 font-mono text-xs text-alert">{error}</p>;
  if (!m) return <p className="font-mono text-xs uppercase tracking-widest text-dim">loading metrics…</p>;

  const t = m.time_to_first_play;
  const empty = m.announcements.total === 0;
  return (
    <main className="space-y-4" data-testid="metrics">
      {empty && (
        <p className="rounded-sm border border-ink-600 bg-ink-900 p-3 text-sm text-dim">
          Nothing generated yet. Run a scenario and these fill in.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile
          id="generated"
          label="Announcements generated"
          value={String(m.announcements.total)}
          sub={`${m.announcements.llm} by the LLM · ${m.announcements.template} from templates · ${m.events} events`}
        />
        <Tile
          id="cache"
          label="Audio cache hit rate"
          value={percent(m.tts_cache_hit_rate)}
          tone="ok"
          sub={`${m.tts_renders} announcements voiced · ${percent(m.tts_segment_hit_rate)} of segments reused`}
        />
        <Tile
          id="saved"
          label="Characters saved"
          value={m.tts_characters_saved.toLocaleString()}
          tone="phosphor"
          sub={`${money(m.tts_cost_saved_usd)} not spent · ${m.tts_characters_billed.toLocaleString()} billed (${money(m.tts_cost_usd)})`}
        />
        <Tile
          id="latency"
          label="Median time to audio"
          value={t.p50_s === null ? "-" : `${t.p50_s.toFixed(2)} s`}
          tone="info"
          sub={
            t.n === 0
              ? "no plays yet"
              : `event received → first play · p95 ${t.p95_s?.toFixed(2)} s · ${t.n} events · TTS first byte ${Math.round(m.avg_tts_ttfa_ms)} ms`
          }
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Model routing · script writing" right={<Tag>{m.llm_calls} calls</Tag>}>
          <Split
            parts={m.llm_routes.map((r) => ({
              label: `${r.role} · ${r.model}`,
              value: r.calls,
              tone: r.role === "default" ? "info" : "phosphor",
            }))}
            unit="calls"
          />
          <p className="mt-2 text-xs text-dim">
            The default model writes first. The escalation model only retries a rejected draft ({percent(m.llm_escalation_share)} of calls).
          </p>
          <Table
            head={["model", "calls", "accepted", "tokens in/out", "cost"]}
            rows={m.llm_routes.map((r) => [
              `${r.role} · ${r.model}`,
              r.calls,
              r.accepted,
              `${r.input_tokens.toLocaleString()} / ${r.output_tokens.toLocaleString()}`,
              money(r.cost_usd),
            ])}
          />
        </Panel>

        <Panel title="Model routing · voice" right={<Tag>{m.tts_renders} renders</Tag>}>
          <Split
            parts={m.tts_models.map((r) => ({
              label: `${r.role} · ${r.model}`,
              value: r.renders,
              tone: r.role === "live" ? "info" : "phosphor",
            }))}
            unit="renders"
          />
          <p className="mt-2 text-xs text-dim">
            Live (first play, updates) uses the low-latency model. Escalation repeats use the highest-quality one.
          </p>
          <Table
            head={["model", "renders", "characters", "cost"]}
            rows={m.tts_models.map((r) => [
              `${r.role} · ${r.model}`,
              r.renders,
              r.characters_billed.toLocaleString(),
              money(r.cost_usd),
            ])}
          />
        </Panel>

        <Panel title="Fact lock" right={<Tag tone={m.announcements.rejected ? "alert" : "ok"}>{m.announcements.rejected} rejected</Tag>}>
          <div className="grid grid-cols-2 gap-3">
            <Stat k="Rejection rate" v={percent(m.announcements.rejection_rate)} />
            <Stat k="Template fallback rate" v={percent(m.announcements.template_rate)} />
          </div>
          <h4 className="mb-1 mt-3 font-mono text-[10px] uppercase tracking-[0.16em] text-dim">Violations by layer</h4>
          {Object.keys(m.announcements.by_layer).length === 0 ? (
            <p className="text-xs text-dim">None.</p>
          ) : (
            <Bars
              items={Object.entries(m.announcements.by_layer)
                .sort((a, b) => b[1] - a[1])
                .map(([k, v]) => ({ label: k, value: v }))}
            />
          )}
        </Panel>

        <Panel title="Cost" right={<Tag>{m.plays} plays</Tag>}>
          <div className="grid grid-cols-3 gap-3">
            <Stat k="LLM" v={money(m.llm_cost_usd)} />
            <Stat k="Voice" v={money(m.tts_cost_usd)} />
            <Stat k="Total" v={money(m.total_cost_usd)} tone="phosphor" />
          </div>
          <p className="mt-2 text-xs text-dim">
            The cache saved {money(m.tts_cost_saved_usd)}. {m.plays} plays came from {m.tts_renders} voiced announcements, so
            repeats cost nothing. Previews before broadcast are not counted.
          </p>
        </Panel>
      </div>

      <Panel title="Per announcement" right={<Tag>latest {rows.length}</Tag>}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left font-mono text-[11px]" data-testid="rows">
            <thead>
              <tr className="border-b border-ink-600 text-[10px] uppercase tracking-wider text-dim">
                {["id", "event", "lang", "level", "source", "model", "rejected", "voice", "cache", "chars", "cost", "first byte"].map((h) => (
                  <th key={h} className="px-2 py-1 font-semibold">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-ink-700 text-slate-300">
                  <td className="px-2 py-1 text-dim">{r.id}</td>
                  <td className="px-2 py-1">
                    #{r.event_id} v{r.version} {r.event_type.replace("_", " ")}
                  </td>
                  <td className="px-2 py-1 uppercase">{r.language}</td>
                  <td className="px-2 py-1">{r.is_resolution ? "res" : r.urgency}</td>
                  <td className="px-2 py-1">
                    <Tag tone={r.source === "llm" ? "info" : "phosphor"}>{r.source}</Tag>
                  </td>
                  <td className="px-2 py-1 text-dim">{r.model ?? "-"}</td>
                  <td className="px-2 py-1 text-alert">{r.rejection_layers.join(", ") || <span className="text-dim">-</span>}</td>
                  <td className="px-2 py-1 text-dim">{r.tts_model ?? "-"}</td>
                  <td className={`px-2 py-1 ${r.cache_hit ? "text-ok" : "text-dim"}`}>
                    {r.cache_hit === null ? "-" : r.cache_hit ? "hit" : "miss"}
                  </td>
                  <td className="px-2 py-1">
                    {r.characters_billed ?? "-"}
                    {r.characters_saved ? <span className="text-ok"> (+{r.characters_saved} saved)</span> : null}
                  </td>
                  <td className="px-2 py-1">{r.tts_cost_usd === null ? "-" : money(r.tts_cost_usd)}</td>
                  <td className="px-2 py-1 text-dim">{r.ttfa_ms === null ? "-" : `${Math.round(r.ttfa_ms)} ms`}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={12} className="px-2 py-4 text-center text-dim">
                    no announcements yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </main>
  );
}

const TILE_TEXT: Record<string, string> = { ok: "text-ok", phosphor: "text-phosphor", info: "text-info" };

function Tile({ id, label, value, sub, tone }: { id: string; label: string; value: string; sub: string; tone?: Tone }) {
  return (
    <div data-testid={`tile-${id}`} className="rounded-md border border-ink-600 bg-ink-900/90 p-3">
      <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-dim">{label}</div>
      <div className={`mt-1 font-mono text-3xl font-bold tabular-nums ${tone ? TILE_TEXT[tone] : "text-slate-100"}`}>{value}</div>
      <div className="mt-1 text-[11px] leading-snug text-dim">{sub}</div>
    </div>
  );
}

function Stat({ k, v, tone }: { k: string; v: string; tone?: Tone }) {
  return (
    <div>
      <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-dim">{k}</div>
      <div className={`font-mono text-xl font-bold tabular-nums ${tone ? TILE_TEXT[tone] : "text-slate-100"}`}>{v}</div>
    </div>
  );
}

const FILL: Record<string, string> = { info: "bg-info", phosphor: "bg-phosphor", ok: "bg-ok", alert: "bg-alert", dim: "bg-dim" };

/** One bar cut into shares, with a legend. Colour and the label both say which part is which. */
function Split({ parts, unit }: { parts: { label: string; value: number; tone: Tone }[]; unit: string }) {
  const total = parts.reduce((n, p) => n + p.value, 0);
  if (total === 0) return <p className="text-xs text-dim">No {unit} yet.</p>;
  return (
    <div>
      <div className="flex h-3 overflow-hidden rounded-sm bg-ink-700">
        {parts.map((p) => (
          <div key={p.label} className={FILL[p.tone]} style={{ width: `${(p.value / total) * 100}%` }} title={`${p.label}: ${p.value}`} />
        ))}
      </div>
      <ul className="mt-2 space-y-0.5">
        {parts.map((p) => (
          <li key={p.label} className="flex items-center gap-2 font-mono text-[11px]">
            <span className={`h-2 w-2 rounded-sm ${FILL[p.tone]}`} />
            <span className="text-slate-300">{p.label}</span>
            <span className="ml-auto text-dim">
              {p.value} {unit} · {percent(p.value / total)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Bars({ items }: { items: { label: string; value: number }[] }) {
  const max = Math.max(...items.map((i) => i.value));
  return (
    <ul className="space-y-1">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-2 font-mono text-[11px]">
          <span className="w-28 shrink-0 text-slate-300">{i.label}</span>
          <span className="h-2 flex-1 rounded-sm bg-ink-700">
            <span className="block h-2 rounded-sm bg-alert" style={{ width: `${(i.value / max) * 100}%` }} />
          </span>
          <span className="w-6 text-right text-dim">{i.value}</span>
        </li>
      ))}
    </ul>
  );
}

function Table({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  if (rows.length === 0) return null;
  return (
    <table className="mt-3 w-full text-left font-mono text-[11px]">
      <thead>
        <tr className="border-b border-ink-600 text-[10px] uppercase tracking-wider text-dim">
          {head.map((h) => (
            <th key={h} className="py-1 pr-2 font-semibold">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-ink-700 text-slate-300">
            {r.map((c, j) => (
              <td key={j} className="py-1 pr-2">
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
