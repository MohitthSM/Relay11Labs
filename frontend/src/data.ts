// The data the pages share, fetched once at the top of the app. Everything is refreshed when the
// WebSocket says something changed (and after it reconnects), never on a timer, except the zones'
// on-air state, which ends on the server's clock and has no message of its own.
import { useCallback, useEffect, useRef, useState } from "react";
import { listEvents, listZones } from "./api";
import type { Live } from "./live";
import type { RelayEvent, Zone } from "./types";

export function useNow(intervalMs = 500): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function useZones(live: Live): Zone[] {
  const [zones, setZones] = useState<Zone[]>([]);
  const refresh = useCallback(() => {
    listZones().then(setZones).catch(() => {});
  }, []);
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 1000);
    const unsubscribe = live.subscribe((m) => {
      if (m.type !== "snapshot") refresh();
    });
    return () => {
      window.clearInterval(timer);
      unsubscribe();
    };
  }, [live, refresh]);
  return zones;
}

export type EventsData = {
  active: RelayEvent[]; // with their announcements
  recent: RelayEvent[]; // resolved or withdrawn, newest first, without announcements
  error: string | null;
  refresh: () => void;
};

export function useEvents(live: Live): EventsData {
  const [active, setActive] = useState<RelayEvent[]>([]);
  const [recent, setRecent] = useState<RelayEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number>();

  const load = useCallback(() => {
    Promise.all([listEvents("active"), listEvents("resolved", false, 8), listEvents("withdrawn", false, 8)])
      .then(([a, r, w]) => {
        setActive(a);
        setRecent([...r, ...w].sort((x, y) => y.id - x.id).slice(0, 8));
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  // bursts of messages (a scenario step creates, approves, schedules) become one fetch
  const refresh = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, 120);
  }, [load]);

  useEffect(() => {
    load();
    const unsubscribe = live.subscribe((m) => {
      if (m.type.startsWith("event.")) refresh();
    });
    return () => {
      unsubscribe();
      window.clearTimeout(timer.current);
    };
  }, [live, load, refresh]);

  // after a dropped connection comes back, catch up on what was missed
  useEffect(() => {
    if (live.status === "open") load();
  }, [live.status, load]);

  return { active, recent, error, refresh };
}
