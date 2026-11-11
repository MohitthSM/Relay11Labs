import type { ReactNode } from "react";

// A handful of small building blocks so the pages read like a control room: status LEDs, framed
// panels with a mono caption, tags. Plain components, utility classes only.

export type Tone = "ok" | "alert" | "info" | "phosphor" | "dim";

const TEXT: Record<Tone, string> = {
  ok: "text-ok",
  alert: "text-alert",
  info: "text-info",
  phosphor: "text-phosphor",
  dim: "text-dim",
};
const BORDER: Record<Tone, string> = {
  ok: "border-ok/40 bg-ok/10",
  alert: "border-alert/50 bg-alert/10",
  info: "border-info/40 bg-info/10",
  phosphor: "border-phosphor/50 bg-phosphor/10",
  dim: "border-ink-500 bg-ink-700",
};
const DOT: Record<Tone, string> = {
  ok: "bg-ok shadow-[0_0_8px_2px_rgba(56,224,140,0.55)]",
  alert: "bg-alert shadow-[0_0_8px_2px_rgba(255,77,90,0.55)]",
  info: "bg-info shadow-[0_0_8px_2px_rgba(76,195,255,0.5)]",
  phosphor: "bg-phosphor shadow-[0_0_10px_3px_rgba(255,176,32,0.6)]",
  dim: "bg-ink-500",
};

export function Led({ tone, pulse = false }: { tone: Tone; pulse?: boolean }) {
  return (
    <span
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${DOT[tone]} ${pulse ? "animate-glow" : ""}`}
    />
  );
}

export function Tag({ tone = "dim", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-sm border px-1.5 py-px font-mono text-[10px] font-semibold uppercase tracking-wider ${BORDER[tone]} ${TEXT[tone]}`}
    >
      {children}
    </span>
  );
}

export function Panel({
  title,
  right,
  children,
  className = "",
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-md border border-ink-600 bg-ink-900/90 ${className}`}>
      <header className="flex items-center justify-between gap-2 border-b border-ink-600 px-3 py-1.5">
        <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-dim">{title}</h2>
        {right}
      </header>
      <div className="p-3">{children}</div>
    </section>
  );
}

export const BTN =
  "rounded-sm border border-ink-500 bg-ink-700 px-2.5 py-1 font-mono text-[11px] font-semibold uppercase tracking-wider text-slate-200 transition hover:border-slate-400 hover:bg-ink-600 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-ink-500 disabled:hover:bg-ink-700";
export const BTN_PRIMARY =
  "rounded-sm border border-phosphor bg-phosphor px-3 py-1 font-mono text-[11px] font-bold uppercase tracking-wider text-ink-950 transition hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-phosphor";
export const BTN_ALERT =
  "rounded-sm border border-alert bg-alert px-2.5 py-1 font-mono text-[11px] font-bold uppercase tracking-wider text-ink-950 transition hover:bg-red-400 disabled:opacity-40";
export const INPUT =
  "w-full rounded-sm border border-ink-500 bg-ink-950 px-2 py-1.5 font-mono text-sm text-slate-100 outline-none placeholder:text-ink-500 focus:border-phosphor disabled:opacity-50";
export const LABEL = "mb-1 block font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-dim";
