import type { Activity } from "./zoneAudio";
import type { RelayEvent, Zone } from "./types";

// Where an event stands, read from the event itself and from the zones' live state.
//   pending    waiting for an operator to approve it (D20); nothing has played
//   playing    a clip of it is on air, or waiting in a zone queue
//   scheduled  nothing on air now; its next repeat is due
//   played     all its repeats are done; it expires or is stopped
//   resolved   stopped, or expired (a resolution message may still be playing)
export type Status = "pending" | "playing" | "scheduled" | "played" | "resolved";

export type EventStanding = {
  status: Status;
  nextAt: string | null; // the soonest next repeat across zones
  playingIn: string[]; // names of zones where it is on air now
};

export function standing(e: RelayEvent, zones: Zone[], activity: Record<string, Activity>): EventStanding {
  // what the server says is on air, plus what this browser has just lit (at high speeds a clip's
  // server-side slot is over before the next refresh)
  const lit = Object.values(activity).filter((a) => a.cmd.event_id === e.id).map((a) => a.cmd.zone_id);
  const playingIn = zones.filter((z) => z.on_air?.event_id === e.id || lit.includes(z.zone_id)).map((z) => z.name);
  const queued = zones.some((z) => z.queue.some((q) => q.event_id === e.id));
  const times = zones
    .filter((z) => z.next?.event_id === e.id)
    .map((z) => z.next!.at)
    .sort();
  const nextAt = times[0] ?? null;

  let status: Status;
  if (e.state !== "active") status = "resolved";
  else if (e.approval === "pending") status = "pending";
  else if (playingIn.length > 0 || queued) status = "playing";
  else if (nextAt) status = "scheduled";
  else status = "played";
  return { status, nextAt, playingIn };
}

const ORDER: Record<Status, number> = { pending: 0, playing: 1, scheduled: 2, played: 3, resolved: 4 };

/** Board order: what needs an operator first, then what is on air, then the rest. */
export function byUrgency(a: { e: RelayEvent; s: EventStanding }, b: { e: RelayEvent; s: EventStanding }) {
  return ORDER[a.s.status] - ORDER[b.s.status] || b.e.id - a.e.id;
}
