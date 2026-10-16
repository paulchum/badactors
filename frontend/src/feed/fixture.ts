// A small hand-built feed for tests: three rooms, four seats, and whatever
// entries a test adds. Not used by the site.

import type { Feed, FeedEvent, FeedSeat, Frame, Phase, Whereabouts } from "./types";

type EventInit = Partial<FeedEvent> & Pick<FeedEvent, "tick" | "type">;

export function seat(id: string, n: number, extra: Partial<FeedSeat> = {}): FeedSeat {
  const character = id[0].toUpperCase() + id.slice(1);
  return {
    id,
    seat: n,
    character,
    audience: `Reference bot #${n}`,
    colour: "#8A8F98",
    agent: "bot:heuristic",
    played_by: "bot",
    model: null,
    build: "tall",
    garment: "grey coat",
    detail: null,
    role: "faithful",
    traitor_from: null,
    recruited_by: null,
    fate: "survived",
    out_at: null,
    decisions: 0,
    refused: 0,
    failed: 0,
    ...extra,
  };
}

/** A feed whose events are the given ones, numbered in order, with positions
 * the given frames or everyone in the hall at every tick. */
export function smallFeed(options: {
  seats?: FeedSeat[];
  events?: EventInit[];
  frames?: Record<number, Record<string, Partial<Whereabouts>>>;
  ticks?: number;
}): Feed {
  const seats = options.seats ?? [seat("ada", 1), seat("bo", 2), seat("cy", 3), seat("di", 4)];
  const events: FeedEvent[] = (options.events ?? []).map((init, index) => ({
    seq: index + 1,
    day: 1,
    phase: "free_time" as Phase,
    actor: null,
    room: null,
    light: null,
    text: init.type,
    quote: null,
    payload: {},
    private: {},
    witnesses: [],
    rolls: [],
    ...init,
  }));
  const last = Math.max(options.ticks ?? 0, ...events.map((event) => event.tick));
  const positions: Frame[] = [];
  let here: Record<string, Whereabouts> = Object.fromEntries(
    seats.map((s) => [s.id, { room: "hall", origin: null, destination: null, asleep: false, alive: true }]),
  );
  for (let tick = 0; tick <= last; tick += 1) {
    const change = options.frames?.[tick] ?? {};
    here = { ...here };
    for (const [id, where] of Object.entries(change)) here[id] = { ...here[id], ...where };
    positions.push({ tick, day: 1, phase: "free_time", seats: here });
  }
  return {
    feed: "badactors.castle.feed",
    version: 1,
    game: {
      world_id: "test",
      castle_id: "test",
      castle: "Test House",
      seed: 1,
      outcome: "finished",
      rated: false,
      winner: "faithful",
      reference_bots: seats.every((s) => s.played_by === "bot"),
      days: 1,
      ticks: last,
      pot: 0,
      payouts: {},
      starting_traitors: seats.filter((s) => s.role === "traitor").map((s) => s.id),
    },
    seats,
    rooms: [
      { id: "hall", name: "Hall", lighting: "bright", capacity: 12, camera: true, private: false },
      { id: "study", name: "Study", lighting: "dim", capacity: 3, camera: false, private: true },
      { id: "crypt", name: "Crypt", lighting: "dark", capacity: 4, camera: false, private: true },
    ],
    passages: [
      { rooms: ["hall", "study"], travel_ticks: 1 },
      { rooms: ["hall", "crypt"], travel_ticks: 3 },
    ],
    things: [
      { id: "coat_1", kind: "garment", name: "grey coat" },
      { id: "shawl_1", kind: "garment", name: "dark shawl" },
      { id: "seal", kind: "seal", name: "Steward's seal" },
    ],
    events,
    positions,
    seatings: [],
    moments: [],
  };
}
