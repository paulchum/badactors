import { describe, expect, it } from "vitest";

import { bundledGame, bundledIndex } from "./bundled";
import { smallFeed } from "./fixture";
import { FeedError, Game, gamesUrl, loadFeed, loadIndex, parseFeed } from "./load";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function fakeFetch(files: Record<string, unknown>) {
  const asked: string[] = [];
  const fetcher = async (url: string) => {
    asked.push(url);
    const body = files[url];
    return { ok: body !== undefined, status: body === undefined ? 404 : 200, json: async () => clone(body) };
  };
  return { fetcher, asked };
}

describe("parseFeed", () => {
  it("accepts a version-2 feed", () => {
    const feed = smallFeed({ events: [{ tick: 0, type: "placed" }], ticks: 2 });
    expect(parseFeed(clone(feed)).positions).toHaveLength(3);
  });

  it("refuses what is not a feed, or another version", () => {
    expect(() => parseFeed({ hello: 1 })).toThrow(FeedError);
    expect(() => parseFeed({ ...smallFeed({}), version: 1 })).toThrow(/version 1/);
  });

  it("refuses events with a gap", () => {
    const feed = smallFeed({ events: [{ tick: 0, type: "a" }, { tick: 0, type: "b" }] });
    feed.events[1].seq = 3;
    expect(() => parseFeed(feed)).toThrow(/no gaps/);
  });

  it("refuses positions that skip a seat or a tick", () => {
    const feed = smallFeed({ ticks: 2 });
    feed.positions.splice(1, 1);
    expect(() => parseFeed(feed)).toThrow(/one frame per tick/);
    const missing = smallFeed({ ticks: 1 });
    delete missing.positions[1].seats.ada;
    expect(() => parseFeed(missing)).toThrow(/ada/);
  });

  it("refuses a moment or a claim pointing at an event the game doesn't have", () => {
    const feed = smallFeed({ events: [{ tick: 0, type: "a" }] });
    feed.moments.push({
      id: "refusal-9",
      kind: "refusal",
      label: "REFUSED",
      headline: "",
      day: 1,
      anchor: 9,
      first_seq: 1,
      last_seq: 9,
      first_tick: 0,
      last_tick: 0,
      seats: [],
      deceived: [],
      stakes: 1,
      contradiction: false,
      score: 1,
      checked: false,
      truth: [],
      evidence: [],
      notes: {},
    });
    expect(() => parseFeed(feed)).toThrow(/Moment refusal-9 points at event 9/);
    const claimed = bundledGame("seed-56.json").feed;
    const broken = clone(claimed);
    broken.claims[0].proof = [broken.events.length + 1];
    expect(() => parseFeed(broken)).toThrow(new RegExp(`Claim ${claimed.claims[0].id} points at event`));
  });
});

describe("loading", () => {
  it("reads the index, then a feed, from beside the page", async () => {
    const feed = smallFeed({ ticks: 1 });
    const { fetcher, asked } = fakeFetch({
      "./games/index.json": { version: 2, games: [{ id: "seed-1", file: "seed-1.json" }] },
      "./games/seed-1.json": feed,
    });
    const index = await loadIndex(fetcher, "./");
    const loaded = await loadFeed(index.games[0], fetcher, "./");
    expect(loaded.game.castle).toBe("Test House");
    expect(asked).toEqual(["./games/index.json", "./games/seed-1.json"]);
  });

  it("says which file could not be loaded", async () => {
    const { fetcher } = fakeFetch({});
    await expect(loadFeed({ file: "nope.json" }, fetcher, "/badactors/")).rejects.toThrow("/badactors/games/nope.json (404)");
    expect(gamesUrl("x.json", "/badactors")).toBe("/badactors/games/x.json");
  });
});

describe("the bundled games", () => {
  const index = bundledIndex();

  it("are all reference-bot games, and every one loads", () => {
    expect(index.games.length).toBeGreaterThanOrEqual(4);
    for (const summary of index.games) {
      const game = bundledGame(summary.file);
      expect(summary.reference_bots).toBe(true);
      expect(game.feed.game.reference_bots).toBe(true);
      expect(game.feed.seats.every((seat) => seat.played_by === "bot" && seat.model == null)).toBe(true);
      expect(game.feed.game.seed).toBe(summary.seed);
      expect(game.lastTick).toBe(summary.ticks);
      expect(game.feed.game.fact_checked).toBe(true);
      expect(game.feed.claims).toHaveLength(summary.claims);
    }
  });

  it("include seed 56, which ends with a recruit still at the table", () => {
    const game = bundledGame("seed-56.json");
    const twist = game.feed.moments.find((moment) => moment.kind === "twist_ending");
    expect(twist?.headline).toContain("still among them");
    expect(index.games[0].id).toBe("seed-56");
  });

  it("know when each seat turned traitor", () => {
    const game: Game = bundledGame("seed-56.json");
    const recruit = game.feed.seats.find((seat) => seat.recruited_by != null)!;
    const turned = recruit.traitor_from!;
    expect(game.traitorAt(recruit.id, turned - 1)).toBe(false);
    expect(game.traitorAt(recruit.id, turned)).toBe(true);
  });
});
