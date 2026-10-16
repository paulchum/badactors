import { describe, expect, it } from "vitest";

import { bundledGame, bundledIndex } from "./bundled";
import { seat, smallFeed } from "./fixture";
import { tokenTimeline, tokensAt } from "./positions";

describe("tokens at a tick", () => {
  const feed = smallFeed({
    // Bo turns traitor at entry 3, on tick 2.
    seats: [seat("ada", 1), seat("bo", 2, { role: "traitor", traitor_from: 3 }), seat("cy", 3), seat("di", 4)],
    events: [
      { tick: 0, type: "placed", payload: { thing_id: "coat_1", where: "worn", wearer_id: "ada" } },
      { tick: 0, type: "placed", payload: { thing_id: "shawl_1", where: "worn", wearer_id: "bo" } },
      { tick: 2, type: "recruit_answer", actor: "bo" },
      { tick: 2, type: "move_depart", actor: "cy", room: "hall", payload: { destination: "crypt", travel_ticks: "3" } },
      { tick: 3, type: "take_off", actor: "ada", payload: { thing_id: "coat_1", where: "held", holder_id: "ada" } },
      { tick: 4, type: "move_arrive", actor: "cy", room: "crypt" },
      { tick: 4, type: "take_off", actor: "bo", payload: { thing_id: "shawl_1", where: "held", holder_id: "bo" } },
      { tick: 4, type: "wear", actor: "bo", payload: { thing_id: "coat_1", where: "worn", wearer_id: "bo" } },
      { tick: 5, type: "sleep", actor: "di" },
      { tick: 5, type: "death", payload: { player: "ada" } },
    ],
    frames: {
      2: { cy: { room: null, origin: "hall", destination: "crypt" } },
      4: { cy: { room: "crypt", origin: null, destination: null } },
      5: { di: { asleep: true }, ada: { alive: false } },
    },
  });
  const timeline = tokenTimeline(feed);
  const at = (tick: number, id: string) => tokensAt(timeline, tick).find((token) => token.id === id)!;

  it("has one list per tick, in seat order", () => {
    expect(timeline).toHaveLength(6);
    expect(tokensAt(timeline, 0).map((token) => token.id)).toEqual(["ada", "bo", "cy", "di"]);
  });

  it("walks a player along the passage, a share per tick", () => {
    expect(at(1, "cy").room).toBe("hall");
    expect(at(2, "cy")).toMatchObject({ room: null, origin: "hall", destination: "crypt" });
    expect(at(2, "cy").progress).toBeCloseTo(1 / 3);
    expect(at(3, "cy").progress).toBeCloseTo(2 / 3);
    expect(at(4, "cy")).toMatchObject({ room: "crypt", origin: null, progress: 0 });
  });

  it("follows each garment's custody", () => {
    expect(at(0, "ada").garment).toBe("grey coat");
    expect(at(3, "ada").garment).toBeNull();
    expect(at(3, "bo").garment).toBe("dark shawl");
    expect(at(4, "bo").garment).toBe("grey coat");
  });

  it("marks a traitor from the entry that made them one", () => {
    expect(at(1, "bo").traitor).toBe(false);
    expect(at(2, "bo").traitor).toBe(true);
    expect(at(5, "ada").traitor).toBe(false);
  });

  it("keeps the asleep and the dead where they lie", () => {
    expect(at(5, "di")).toMatchObject({ asleep: true, alive: true, room: "hall" });
    expect(at(5, "ada")).toMatchObject({ alive: false, room: "hall" });
  });

  it("clamps a tick outside the game", () => {
    expect(tokensAt(timeline, -4)).toBe(timeline[0]);
    expect(tokensAt(timeline, 99)).toBe(timeline[5]);
  });
});

describe("tokens in the bundled games", () => {
  it("stand where the feed's frames put them, and wear one garment each", () => {
    for (const summary of bundledIndex().games) {
      const { feed } = bundledGame(summary.file);
      const timeline = tokenTimeline(feed);
      expect(timeline).toHaveLength(feed.positions.length);
      timeline.forEach((tokens, tick) => {
        for (const token of tokens) {
          const where = feed.positions[tick].seats[token.id];
          expect(token.room).toBe(where.room ?? null);
          expect(token.alive).toBe(where.alive);
          if (token.room === null) {
            expect(token.progress).toBeGreaterThan(0);
            expect(token.progress).toBeLessThan(1);
          }
        }
      });
      const opening = timeline[0];
      for (const s of feed.seats) {
        expect(opening.find((token) => token.id === s.id)?.garment).toBe(s.garment);
      }
    }
  });
});
