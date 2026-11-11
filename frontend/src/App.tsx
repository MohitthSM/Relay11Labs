import { useEffect, useState } from "react";
import { getHealth } from "./api";
import Console from "./pages/Console";
import type { Health } from "./types";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <div className="mx-auto max-w-5xl p-4">
      <header className="mb-4 flex items-center justify-between border-b pb-2">
        <h1 className="text-xl font-semibold">Relay</h1>
        <span className="rounded bg-slate-200 px-2 py-1 font-mono text-xs" data-testid="health">
          {health ? `LLM: ${health.llm} · TTS: ${health.tts}` : (error ?? "connecting…")}
        </span>
      </header>
      <Console />
    </div>
  );
}
