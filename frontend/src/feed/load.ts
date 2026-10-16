// Loading the bundled games: the index first, then one feed at a time. The site
// is static, so these are plain files beside the page.

import type { Feed, FeedEvent, FeedRoom, FeedSeat, GameIndex, GameSummary } from "./types";

export const FEED_KIND = "badactors.castle.feed";
export const FEED_VERSION = 1;

export class FeedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FeedError";
  }
}

type Fetch = (url: string) => Promise<{ ok: boolean; status: number; json(): Promise<unknown> }>;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function list(raw: Record<string, unknown>, key: string): unknown[] {
  const value = raw[key];
  if (!Array.isArray(value)) throw new FeedError(`The feed has no ${key} list.`);
  return value;
}

/** Check a parsed document is a version-1 audience feed whose parts line up,
 * and return it typed. Throws a FeedError that says what is wrong. */
export function parseFeed(raw: unknown): Feed {
  if (!isObject(raw) || raw.feed !== FEED_KIND) {
    throw new FeedError("This is not a Bad Actors audience feed.");
  }
  if (raw.version !== FEED_VERSION) {
    throw new FeedError(`Feed version ${String(raw.version)} is not one this site reads (${FEED_VERSION}).`);
  }
  if (!isObject(raw.game)) throw new FeedError("The feed has no game.");
  for (const key of ["seats", "rooms", "passages", "things", "events", "positions", "seatings", "moments"]) {
    list(raw, key);
  }
  const feed = raw as unknown as Feed;
  if (feed.seats.length === 0) throw new FeedError("The feed has no seats.");
  feed.events.forEach((event, index) => {
    if (event.seq !== index + 1) {
      throw new FeedError(`Event ${index + 1} has sequence number ${event.seq}; the ledger must be gapless.`);
    }
  });
  const ticks = feed.positions.length;
  if (ticks === 0 || feed.positions[0].tick !== 0 || feed.positions[ticks - 1].tick !== ticks - 1) {
    throw new FeedError("The feed's positions are not one frame per tick from tick 0.");
  }
  const seats = new Set(feed.seats.map((seat) => seat.id));
  for (const frame of [feed.positions[0], feed.positions[ticks - 1]]) {
    for (const id of seats) {
      if (!(id in frame.seats)) throw new FeedError(`Seat ${id} is missing from tick ${frame.tick}.`);
    }
  }
  const events = feed.events.length;
  for (const moment of feed.moments) {
    for (const seq of [moment.anchor, ...moment.truth, ...moment.evidence]) {
      if (seq < 1 || seq > events) throw new FeedError(`Moment ${moment.id} points at entry ${seq}, outside the ledger.`);
    }
  }
  return feed;
}

export function parseIndex(raw: unknown): GameIndex {
  if (!isObject(raw) || raw.version !== 1 || !Array.isArray(raw.games)) {
    throw new FeedError("The game index is missing or not version 1.");
  }
  for (const game of raw.games as unknown[]) {
    if (!isObject(game) || typeof game.id !== "string" || typeof game.file !== "string") {
      throw new FeedError("A game in the index has no id or file.");
    }
  }
  return raw as unknown as GameIndex;
}

const BASE = (import.meta.env?.BASE_URL as string | undefined) ?? "/";

export function gamesUrl(file: string, base: string = BASE): string {
  return `${base.endsWith("/") ? base : `${base}/`}games/${file}`;
}

async function getJson(url: string, fetcher: Fetch): Promise<unknown> {
  const response = await fetcher(url);
  if (!response.ok) throw new FeedError(`Could not load ${url} (${response.status}).`);
  return response.json();
}

const defaultFetch: Fetch = (url) => fetch(url);

export async function loadIndex(fetcher: Fetch = defaultFetch, base: string = BASE): Promise<GameIndex> {
  return parseIndex(await getJson(gamesUrl("index.json", base), fetcher));
}

const cache = new Map<string, Promise<Feed>>();

export function loadFeed(game: Pick<GameSummary, "file">, fetcher: Fetch = defaultFetch, base: string = BASE): Promise<Feed> {
  const url = gamesUrl(game.file, base);
  let pending = cache.get(url);
  if (pending === undefined || fetcher !== defaultFetch) {
    pending = getJson(url, fetcher).then(parseFeed);
    if (fetcher === defaultFetch) {
      cache.set(url, pending);
      pending.catch(() => cache.delete(url));
    }
  }
  return pending;
}

/** Lookups a renderer needs over and over. */
export class Game {
  readonly seats: Map<string, FeedSeat>;
  readonly rooms: Map<string, FeedRoom>;
  readonly things: Map<string, string>;

  constructor(readonly feed: Feed) {
    this.seats = new Map(feed.seats.map((seat) => [seat.id, seat]));
    this.rooms = new Map(feed.rooms.map((room) => [room.id, room]));
    this.things = new Map(feed.things.map((thing) => [thing.id, thing.name]));
  }

  get lastTick(): number {
    return this.feed.positions.length - 1;
  }

  event(seq: number): FeedEvent {
    const event = this.feed.events[seq - 1];
    if (event === undefined) throw new FeedError(`No entry ${seq}.`);
    return event;
  }

  seat(id: string): FeedSeat | undefined {
    return this.seats.get(id);
  }

  roomName(id: string | null | undefined): string {
    return (id && this.rooms.get(id)?.name) || "the castle";
  }

  thingName(id: string): string {
    return this.things.get(id) ?? id.replace(/_/g, " ");
  }

  /** Whether a seat was a traitor when an entry was written. */
  traitorAt(id: string, seq: number): boolean {
    const seat = this.seats.get(id);
    return seat?.role === "traitor" && seat.traitor_from != null && seat.traitor_from <= seq;
  }

  /** Whether a seat was a traitor by the end of a tick. */
  traitorByTick(id: string, tick: number): boolean {
    const seat = this.seats.get(id);
    if (seat?.role !== "traitor" || seat.traitor_from == null) return false;
    return this.event(seat.traitor_from).tick <= tick;
  }
}
