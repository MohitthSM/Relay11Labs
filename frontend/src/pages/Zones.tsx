import { useCallback, useEffect, useRef, useState } from "react";
import { listZones, watchEvents } from "../api";
import ZonePlayer from "../components/ZonePlayer";
import type { PlayCommand, Zone } from "../types";

// One panel per zone. One WebSocket for the page: play commands are handed to the zone's panel.
// The panels' text (on air, waiting, next, held) comes from GET /playback/zones.
export default function Zones() {
  const [zones, setZones] = useState<Zone[]>([]);
  const [audioOn, setAudioOn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listeners = useRef(new Map<string, (cmd: PlayCommand) => void>());

  const refresh = useCallback(() => {
    listZones().then(setZones).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 1000); // on-air captions end on the server's clock
    const close = watchEvents((m) => {
      if (m.type === "play") listeners.current.get(m.zone_id)?.(m);
      if (m.type !== "snapshot") refresh();
    });
    return () => {
      clearInterval(timer);
      close();
    };
  }, [refresh]);

  const subscribe = useCallback((zoneId: string, onPlay: (cmd: PlayCommand) => void) => {
    listeners.current.set(zoneId, onPlay);
    return () => {
      listeners.current.delete(zoneId);
    };
  }, []);

  const sites = [...new Set(zones.map((z) => z.site_id))];
  return (
    <main className="space-y-4">
      {error && <p className="rounded bg-red-100 p-2 text-sm text-red-800">{error}</p>}
      <button
        className={`rounded px-3 py-1 text-sm ${audioOn ? "bg-slate-200" : "bg-blue-700 text-white"}`}
        onClick={() => setAudioOn(!audioOn)} // a click is needed before a browser plays audio
      >
        {audioOn ? "Mute zones" : "Enable zone audio"}
      </button>
      {sites.map((site) => (
        <section key={site}>
          <h2 className="mb-2 font-medium">{site}</h2>
          <ul className="grid gap-2 md:grid-cols-3">
            {zones
              .filter((z) => z.site_id === site)
              .map((z) => (
                <ZonePlayer key={z.zone_id} zone={z} audioOn={audioOn} subscribe={subscribe} />
              ))}
          </ul>
        </section>
      ))}
    </main>
  );
}
