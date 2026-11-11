import { useEffect, useState } from "react";
import { getHealth } from "./api";
import Console from "./pages/Console";
import Zones from "./pages/Zones";
import type { Health } from "./types";

type Page = "console" | "zones";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<Page>("console");
  const [operator, setOperator] = useState("operator"); // recorded as approved_by (D20)

  useEffect(() => {
    getHealth().then(setHealth).catch((e: Error) => setError(e.message));
  }, []);

  const tab = (p: Page, label: string) => (
    <button
      className={`rounded px-2 py-1 ${page === p ? "bg-slate-800 text-white" : "hover:bg-slate-100"}`}
      onClick={() => setPage(p)}
    >
      {label}
    </button>
  );

  return (
    <div className="mx-auto max-w-5xl p-4">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-2 border-b pb-2">
        <h1 className="text-xl font-semibold">Relay</h1>
        <nav className="flex gap-1 text-sm">
          {tab("console", "Console")}
          {tab("zones", "Zones")}
        </nav>
        <label className="text-xs">
          operator{" "}
          <input
            className="w-28 rounded border px-1"
            value={operator}
            onChange={(e) => setOperator(e.target.value)}
          />
        </label>
        <span className="rounded bg-slate-200 px-2 py-1 font-mono text-xs" data-testid="health">
          {health ? `LLM: ${health.llm} · TTS: ${health.tts}` : (error ?? "connecting…")}
        </span>
      </header>
      {page === "console" ? <Console operator={operator} /> : <Zones />}
    </div>
  );
}
