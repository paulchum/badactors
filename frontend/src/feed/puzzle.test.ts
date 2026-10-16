import { describe, expect, it } from "vitest";

import { bundledGame, bundledIndex } from "./bundled";
import { seat, smallFeed } from "./fixture";
import { Game } from "./load";
import { EPOCH, dailyPick, dayNumber, puzzleOfRound, puzzlesFrom, score, shareGrid } from "./puzzle";

// One round table: Bo, a traitor, accuses Ada; Ada accuses Bo; Cy accuses Di.
// The votes: Ada and Cy vote Bo, Di votes Ada, Bo votes Ada, Ed votes Ada.
function table() {
  const words = (actor: string, type: string, target: string | null, quote: string, diary?: string) => ({
    tick: 5,
    type,
    actor,
    room: "hall",
    quote,
    payload: (target ? { target } : {}) as Record<string, string>,
    private: (diary ? { diary } : {}) as Record<string, string>,
  });
  const feed = smallFeed({
    seats: [
      seat("ada", 1),
      seat("bo", 2, { role: "traitor", traitor_from: 1 }),
      seat("cy", 3),
      seat("di", 4),
      seat("ed", 5),
    ],
    events: [
      { tick: 0, type: "roles_drawn", payload: { traitors: "bo" } },
      { tick: 3, type: "poison", actor: "bo", room: "hall", payload: { tool: "poison", target: "ed" } },
      { tick: 5, type: "table_opened" },
      words("bo", "accuse", "ada", "Ada was in the crypt.", "Ada saw too much."),
      words("ada", "accuse", "bo", "Bo is lying."),
      words("cy", "accuse", "di", "Di's story keeps changing.", "Di slipped up."),
      words("bo", "defend", null, "I am Faithful."),
      { tick: 5, type: "vote", actor: "ada", payload: { target: "bo" }, private: { diary: "Bo, surely." } },
      { tick: 5, type: "banishment", payload: { target: "ada", role: "faithful", votes: "ada=bo,bo=ada,cy=bo,di=ada,ed=ada" } },
    ],
  });
  return new Game(feed);
}

describe("puzzles", () => {
  const game = table();
  const [puzzle] = puzzlesFrom(game);

  it("finds the table, the liar and three suspects in the order they spoke", () => {
    expect(puzzle.liar).toBe("bo");
    expect(puzzle.suspects.map((s) => s.id)).toEqual(["bo", "ada", "cy"]);
    expect(puzzle.suspects.filter((s) => s.traitor).map((s) => s.id)).toEqual(["bo"]);
    expect(puzzle.captions).toEqual([4, 5, 6, 7]);
  });

  it("points at the lie's truth: the traitor draw and the dose before it", () => {
    expect(puzzle.truth).toEqual([1, 2]);
  });

  it("keeps each suspect's own line: their words' diary, else their vote's", () => {
    expect(puzzle.suspects.map((s) => s.line)).toEqual([4, 8, 6]);
    expect(puzzle.suspects.map((s) => s.vote)).toEqual(["ada", "bo", "bo"]);
  });

  it("counts only the Faithful's votes, the liar's own and the traitors' left out", () => {
    expect(puzzle.voters).toEqual(["ada", "cy", "di", "ed"]);
    expect(puzzle.caught).toEqual(["ada", "cy"]);
    expect(puzzle.elsewhere).toEqual([]);
  });

  it("skips a suspect who only repeats another's words", () => {
    const echo = new Game(
      smallFeed({
        seats: [seat("ada", 1), seat("bo", 2, { role: "traitor", traitor_from: 1 }), seat("cy", 3), seat("di", 4)],
        events: [
          { tick: 0, type: "roles_drawn" },
          { tick: 1, type: "table_opened" },
          { tick: 1, type: "accuse", actor: "bo", quote: "Ada lies.", payload: { target: "ada" } },
          { tick: 1, type: "accuse", actor: "ada", quote: "Di did it.", payload: { target: "di" } },
          { tick: 1, type: "accuse", actor: "cy", quote: "Di did it.", payload: { target: "di" } },
          { tick: 1, type: "accuse", actor: "di", quote: "Bo is a traitor.", payload: { target: "bo" } },
          { tick: 1, type: "banishment", payload: { target: "di", role: "faithful", votes: "ada=di,bo=di,cy=di,di=bo" } },
        ],
      }),
    );
    const [found] = puzzlesFrom(echo);
    expect(found.suspects.map((s) => s.id)).toEqual(["bo", "ada", "di"]);
  });

  it("scores a pick against the liar", () => {
    expect(score(puzzle, "bo")).toEqual({ correct: true, pick: "bo", liar: "bo", caught: 2, of: 4 });
    expect(score(puzzle, "cy")).toMatchObject({ correct: false, pick: "cy", caught: 2, of: 4 });
    expect(() => score(puzzle, "ed")).toThrow(/not a suspect/);
  });

  it("shares the result without naming anyone", () => {
    const text = shareGrid(puzzle, score(puzzle, "bo"), 3, true);
    expect(text).toBe(
      [
        "Bad Actors · Who is lying? #3",
        "🟩 me: caught the liar",
        "🟩🟩🟥🟥 the table: 2 of 4 caught it",
        "Played by reference bots",
      ].join("\n"),
    );
    for (const seat of game.feed.seats) expect(text).not.toContain(seat.character);
  });

  it("finds none at a table with no lying traitor", () => {
    const honest = smallFeed({
      events: [
        { tick: 1, type: "table_opened" },
        { tick: 1, type: "accuse", actor: "ada", quote: "Bo!", payload: { target: "bo" } },
        { tick: 1, type: "banishment", payload: { target: "bo", role: "faithful", votes: "ada=bo" } },
      ],
    });
    expect(puzzlesFrom(new Game(honest))).toEqual([]);
  });
});

describe("the daily puzzle", () => {
  const games = bundledIndex().games;

  it("numbers days from the first puzzle, by the local date", () => {
    expect(dayNumber(new Date(2026, 9, 16, 0, 1))).toBe(0);
    expect(dayNumber(new Date(2026, 9, 16, 23, 59))).toBe(0);
    expect(dayNumber(new Date(2026, 9, 17, 9))).toBe(1);
    expect(dayNumber(new Date(EPOCH + 365 * 86_400_000 + 43_200_000))).toBe(365);
  });

  it("takes the games in turn, then each game's next puzzle", () => {
    const n = games.length;
    expect(dailyPick(games, 0)).toEqual({ game: games[0], round: 0 });
    expect(dailyPick(games, 1).game).toBe(games[1]);
    expect(dailyPick(games, n)).toEqual({ game: games[0], round: 1 });
    expect(dailyPick(games, -1).game).toBe(games[n - 1]);
  });

  it("finds a puzzle in every bundled game, each with one liar who was a traitor", () => {
    for (const summary of games) {
      const game = bundledGame(summary.file);
      const puzzles = puzzlesFrom(game);
      expect(puzzles.length, summary.id).toBeGreaterThan(0);
      for (const puzzle of puzzles) {
        expect(puzzle.suspects).toHaveLength(3);
        expect(puzzle.suspects.filter((s) => s.traitor)).toHaveLength(1);
        expect(game.traitorAt(puzzle.liar, puzzle.suspects.find((s) => s.traitor)!.words)).toBe(true);
        for (const s of puzzle.suspects.filter((x) => !x.traitor)) expect(game.traitorAt(s.id, s.words)).toBe(false);
        expect(puzzle.truth.length).toBeGreaterThan(0);
        expect(puzzle.caught.length).toBeLessThanOrEqual(puzzle.voters.length);
      }
      expect(puzzleOfRound(puzzles, puzzles.length)).toBe(puzzles[0]);
    }
  });
});
