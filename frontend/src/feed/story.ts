// What the site derives from a feed to tell one moment: the words said, the
// lines written privately, the entries that show what happened, the custody of
// the object in question, the round tables and their votes.

import type { Game } from "./load";
import type { FeedEvent, FeedMoment, Intent, LineField } from "./types";

/** Entry types whose quote is a player's own words to someone else. */
const SPOKEN = new Set([
  "talk",
  "accuse",
  "defend",
  "question",
  "answer",
  "alibi",
  "vouch",
  "report",
  "cross_examine",
  "demand_alibi",
  "whisper",
  "recruit_offer",
  "recruit_answer",
  "nominate",
]);

export const LINE_FIELDS: LineField[] = ["diary", "reason", "goal", "thoughts"];

export const LINE_NAMES: Record<LineField, string> = {
  diary: "diary",
  reason: "private reason",
  goal: "private plan",
  thoughts: "exit interview, unheard by the living",
};

export function isSpoken(event: FeedEvent): boolean {
  return Boolean(event.actor && event.quote && SPOKEN.has(event.type));
}

export function hasPrivate(event: FeedEvent): boolean {
  return Boolean(event.intent) || LINE_FIELDS.some((field) => event.private[field]);
}

/** Every entry in a moment's story, in ledger order. */
export function momentEntries(moment: FeedMoment): number[] {
  const seqs = new Set<number>([moment.anchor, ...moment.truth, ...moment.evidence]);
  if (moment.claim != null) seqs.add(moment.claim);
  if (moment.line) seqs.add(moment.line.seq);
  return [...seqs].sort((a, b) => a - b);
}

export interface Columns {
  said: FeedEvent[];
  privately: FeedEvent[];
  happened: FeedEvent[];
}

/** The three columns of the replay: what was said, what was written privately,
 * and what the ledger shows happened, the truth to rewind to first. */
export function momentColumns(game: Game, moment: FeedMoment): Columns {
  const entries = momentEntries(moment).map((seq) => game.event(seq));
  const truth = new Set(moment.truth);
  const said = entries.filter(isSpoken);
  const privately = entries.filter(hasPrivate);
  const happened = [
    ...moment.truth.map((seq) => game.event(seq)),
    ...entries.filter((event) => !truth.has(event.seq) && !isSpoken(event)),
  ];
  return { said, privately, happened };
}

export function intentText(intent: Intent, name: (id: string) => string): string {
  const window = {
    next_vote: "by the next vote",
    next_phase: "by the next part of the day",
    next_three_actions: "within three acts",
  }[intent.window];
  const want = intent.want;
  const target = name(intent.target);
  const wanted = name(want);
  const effect = {
    vote: `vote for ${wanted}`,
    move: `go to ${want.replace(/_/g, " ")}`,
    search: `search ${want.replace(/_/g, " ")}`,
    belief: `believe ${wanted === want ? `“${want}”` : `${wanted} is a traitor`}`,
    alibi: `vouch for ${wanted}`,
  }[intent.effect];
  return `Meant ${target} to ${effect}, ${window}.`;
}

// ----------------------------------------------------------------- custody

/** Entry types that move an object (castle/ledger.py CUSTODY_TYPES). */
export const CUSTODY = new Set([
  "placed",
  "take",
  "drop",
  "stow",
  "awarded",
  "spent",
  "take_off",
  "wear",
  "plant",
  "steal",
  "destroyed",
]);

export interface Link {
  seq: number;
  tick: number;
  day: number;
  type: string;
  actor: string | null;
  /** Where the entry left the object, or null when it only touched it. */
  where: Placement | null;
  text: string;
}

export type Placement =
  | { kind: "room"; room: string; hidden: boolean }
  | { kind: "held"; by: string }
  | { kind: "worn"; by: string }
  | { kind: "container"; in: string }
  | { kind: "offstage" }
  | { kind: "gone" };

function placementOf(event: FeedEvent): Placement | null {
  const p = event.payload;
  switch (p.where) {
    case "room":
      return { kind: "room", room: p.room, hidden: p.hidden === "true" };
    case "held":
      return { kind: "held", by: p.holder_id };
    case "worn":
      return { kind: "worn", by: p.wearer_id };
    case "container":
      return { kind: "container", in: p.container_id };
    case "offstage":
      return { kind: "offstage" };
    default:
      return event.type === "destroyed" ? { kind: "gone" } : null;
  }
}

function about(event: FeedEvent): string | undefined {
  return event.payload.thing_id ?? event.payload.glass;
}

/** An object's custody chain, with the entries that touched it along the way
 * (an inspection, a dose, a rinse), up to an entry if one is given. */
export function custodyChain(game: Game, thing: string, until?: number): Link[] {
  const chain: Link[] = [];
  for (const event of game.feed.events) {
    if (until !== undefined && event.seq > until) break;
    if (about(event) !== thing) continue;
    const where = CUSTODY.has(event.type) ? placementOf(event) : null;
    chain.push({
      seq: event.seq,
      tick: event.tick,
      day: event.day,
      type: event.type,
      actor: event.actor ?? null,
      where,
      text: event.text,
    });
  }
  return chain;
}

const QUIET_THINGS = new Set(["guest_book", "camera_log", "register", "letters", "steward_desk"]);

/** The object a moment turns on: the first one its truth or its story names. */
export function objectInQuestion(game: Game, moment: FeedMoment): string | null {
  const seqs = [...moment.truth, ...momentEntries(moment)];
  for (const seq of seqs) {
    const thing = about(game.event(seq));
    if (thing && !QUIET_THINGS.has(thing)) return thing;
  }
  for (const seq of seqs) {
    const thing = about(game.event(seq));
    if (thing) return thing;
  }
  return null;
}

export function placementText(where: Placement, game: Game, name: (id: string) => string): string {
  switch (where.kind) {
    case "room":
      return `in the ${game.roomName(where.room)}${where.hidden ? ", hidden" : ""}`;
    case "held":
      return `held by ${name(where.by)}`;
    case "worn":
      return `worn by ${name(where.by)}`;
    case "container":
      return `inside the ${game.thingName(where.in)}`;
    case "offstage":
      return "in the Steward's keeping";
    case "gone":
      return "destroyed";
  }
}

// ------------------------------------------------------------ round tables

export interface Vote {
  voter: string;
  target: string;
}

export interface Table {
  day: number;
  opened: number;
  closed: number;
  banished: string | null;
  role: string | null;
  votes: Vote[];
}

export function parseVotes(raw: string | undefined): Vote[] {
  return (raw ?? "")
    .split(",")
    .map((pair) => pair.split("="))
    .filter((pair) => pair.length === 2 && pair[0] && pair[1])
    .map(([voter, target]) => ({ voter, target }));
}

/** Each round table, from its opening to its first banishment. */
export function tables(game: Game): Table[] {
  const out: Table[] = [];
  const events = game.feed.events;
  events.forEach((event, index) => {
    if (event.type !== "table_opened") return;
    for (const later of events.slice(index + 1)) {
      if (later.type === "table_opened") break;
      if (later.type === "banishment") {
        out.push({
          day: event.day,
          opened: event.seq,
          closed: later.seq,
          banished: later.payload.target ?? null,
          role: later.payload.role ?? null,
          votes: parseVotes(later.payload.votes),
        });
        return;
      }
    }
  });
  return out;
}
