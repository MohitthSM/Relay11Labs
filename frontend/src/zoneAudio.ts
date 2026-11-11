import { useCallback, useEffect, useRef, useState } from "react";
import { audioSrc, getEvent } from "./api";
import type { Live } from "./live";
import type { PlayCommand } from "./types";

// Plays the server's play commands in the browser, one <audio> per zone. The server owns the
// zone clock (D9) and sends one clip at a time. At high demo speeds clips arrive faster than they
// can be heard, so a zone never cuts a clip but keeps only the newest few queued.
//
// It also says which zones are lit. With audio on, a zone is lit while its clip really plays.
// With audio off it is lit for the clip's length at the current speed (at least a moment, so a
// 60x demo still flashes). It lives at the top of the app, so sound continues across pages.

const MAX_QUEUE = 3;
const MIN_LIT_MS = 1400;

export type Activity = {
  cmd: PlayCommand;
  caption: string | null; // the script on air; fetched over HTTP, never sent on the socket
  audible: boolean; // this browser is playing the clip
};

type ZonePlayer = {
  audio: HTMLAudioElement;
  queue: PlayCommand[];
  busy: boolean;
  timer?: number; // clears the light when audio is off
};

export function useZoneAudio(live: Live, enabled: boolean, speed: number): Record<string, Activity> {
  const [activity, setActivity] = useState<Record<string, Activity>>({});
  const players = useRef(new Map<string, ZonePlayer>());
  const captions = useRef(new Map<number, string>()); // announcement id -> script
  const asked = useRef(new Set<string>()); // "event:announcement" fetches already started
  const playNextRef = useRef<(zoneId: string) => void>(() => {});
  const enabledRef = useRef(enabled);
  const speedRef = useRef(speed);
  enabledRef.current = enabled;
  speedRef.current = speed;

  const player = (zoneId: string): ZonePlayer => {
    let p = players.current.get(zoneId);
    if (!p) {
      const audio = new Audio();
      audio.addEventListener("ended", () => playNextRef.current(zoneId));
      p = { audio, queue: [], busy: false };
      players.current.set(zoneId, p);
    }
    return p;
  };

  const clear = useCallback((zoneId: string) => {
    setActivity((prev) => {
      if (!(zoneId in prev)) return prev;
      const rest = { ...prev };
      delete rest[zoneId];
      return rest;
    });
  }, []);

  const light = useCallback(
    (cmd: PlayCommand, audible: boolean) =>
      setActivity((prev) => ({
        ...prev,
        [cmd.zone_id]: { cmd, audible, caption: captions.current.get(cmd.announcement_id) ?? null },
      })),
    [],
  );

  const loadCaption = useCallback((cmd: PlayCommand) => {
    if (captions.current.has(cmd.announcement_id)) return;
    const key = `${cmd.event_id}:${cmd.announcement_id}`;
    if (asked.current.has(key)) return;
    asked.current.add(key);
    getEvent(cmd.event_id)
      .then((e) => {
        for (const a of e.announcements) captions.current.set(a.id, a.script);
        const text = captions.current.get(cmd.announcement_id);
        if (!text) return;
        setActivity((prev) => {
          const a = prev[cmd.zone_id];
          if (!a || a.cmd.announcement_id !== cmd.announcement_id) return prev;
          return { ...prev, [cmd.zone_id]: { ...a, caption: text } };
        });
      })
      .catch(() => asked.current.delete(key));
  }, []);

  const playNext = useCallback(
    (zoneId: string) => {
      const p = player(zoneId);
      const next = p.queue.shift();
      p.busy = Boolean(next);
      if (!next) {
        clear(zoneId);
        return;
      }
      light(next, true);
      p.audio.src = audioSrc(next.url);
      p.audio.play().catch(() => playNextRef.current(zoneId)); // blocked or broken clip: move on
    },
    [clear, light],
  );
  playNextRef.current = playNext;

  useEffect(
    () =>
      live.subscribe((m) => {
        if (m.type !== "play") return;
        loadCaption(m);
        const p = player(m.zone_id);
        if (enabledRef.current) {
          p.queue.push(m);
          p.queue.splice(0, Math.max(0, p.queue.length - MAX_QUEUE));
          if (!p.busy) playNext(m.zone_id);
        } else {
          light(m, false);
          window.clearTimeout(p.timer);
          p.timer = window.setTimeout(
            () => clear(m.zone_id),
            Math.max(MIN_LIT_MS, (m.duration_s * 1000) / speedRef.current),
          );
        }
      }),
    [live, loadCaption, playNext, light, clear],
  );

  // switching audio on or off silences every zone at once and starts clean
  useEffect(() => {
    for (const [zoneId, p] of players.current) {
      p.queue = [];
      p.busy = false;
      p.audio.pause();
      window.clearTimeout(p.timer);
      clear(zoneId);
    }
  }, [enabled, clear]);

  return activity;
}
