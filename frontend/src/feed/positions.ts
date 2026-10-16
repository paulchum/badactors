// Where every token stands at the end of a tick. The feed gives each seat's
// room, or the two rooms it is walking between, per tick; the ledger adds how
// far along the walk is, which garment each player has on, and whether they
// had turned traitor yet. This folds those in, tick by tick.

import type { Feed, Whereabouts } from "./types";

export interface Token {
  id: string;
  seat: number;
  /** The room they stand in, or null on the way between two. */
  room: string | null;
  origin: string | null;
  destination: string | null;
  /** How far along the walk, from just past 0 to just under 1. */
  progress: number;
  asleep: boolean;
  alive: boolean;
  /** The outer garment worn at the end of the tick, or null in shirtsleeves. */
  garment: string | null;
  traitor: boolean;
}

interface Walk {
  tick: number;
  travel: number;
}

/** One list of tokens per tick, in seat order, for the whole game. */
export function tokenTimeline(feed: Feed): Token[][] {
  const garments = new Map(feed.things.filter((thing) => thing.kind === "garment").map((thing) => [thing.id, thing.name]));
  const wearer = new Map<string, string>();
  const walks = new Map<string, Walk>();
  const traitors = new Set<string>();
  const turns = new Map<number, string>();
  for (const seat of feed.seats) {
    if (seat.role === "traitor" && seat.traitor_from != null) turns.set(seat.traitor_from, seat.id);
  }
  const seats = [...feed.seats].sort((a, b) => a.seat - b.seat);
  const timeline: Token[][] = [];
  let index = 0;
  for (const frame of feed.positions) {
    while (index < feed.events.length && feed.events[index].tick <= frame.tick) {
      const event = feed.events[index];
      index += 1;
      const turned = turns.get(event.seq);
      if (turned !== undefined) traitors.add(turned);
      const thing = event.payload.thing_id;
      if (thing !== undefined && garments.has(thing) && event.payload.where !== undefined) {
        if (event.payload.where === "worn" && event.payload.wearer_id) wearer.set(thing, event.payload.wearer_id);
        else wearer.delete(thing);
      }
      if (event.type === "move_depart" && event.actor) {
        walks.set(event.actor, { tick: event.tick, travel: Number(event.payload.travel_ticks) || 1 });
      }
    }
    const worn = new Map<string, string>();
    for (const [thing, pid] of wearer) worn.set(pid, garments.get(thing) ?? thing);
    timeline.push(
      seats.map((seat) => {
        const where: Whereabouts = frame.seats[seat.id] ?? { asleep: false, alive: false };
        const walking = !where.room && where.origin != null && where.destination != null;
        return {
          id: seat.id,
          seat: seat.seat,
          room: where.room ?? null,
          origin: walking ? (where.origin ?? null) : null,
          destination: walking ? (where.destination ?? null) : null,
          progress: walking ? progressOf(walks.get(seat.id), frame.tick) : 0,
          asleep: where.asleep,
          alive: where.alive,
          garment: worn.get(seat.id) ?? null,
          traitor: traitors.has(seat.id),
        };
      }),
    );
  }
  return timeline;
}

/** A walk of k ticks set out on tick d arrives on tick d + k - 1; at the end of
 * tick t on the way it is (t - d + 1) / k of the way there. */
function progressOf(walk: Walk | undefined, tick: number): number {
  if (walk === undefined) return 0.5;
  const share = (tick - walk.tick + 1) / Math.max(walk.travel, 1);
  return Math.min(Math.max(share, 0.05), 0.95);
}

/** The tokens at the end of one tick, clamped to the game. */
export function tokensAt(timeline: Token[][], tick: number): Token[] {
  if (timeline.length === 0) return [];
  return timeline[Math.min(Math.max(Math.round(tick), 0), timeline.length - 1)];
}
