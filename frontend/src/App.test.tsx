import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { bundledGame, bundledIndex } from "./feed/bundled";
import { dailyPick, puzzleOfRound, puzzlesFrom } from "./feed/puzzle";
import { Stage } from "./landing/Landing";
import { parseRoute, routeSearch } from "./route";
import { Replay } from "./viewer/Viewer";

describe("App", () => {
  it("opens on the landing game with the brand", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("BAD");
    expect(html).toContain("ACTORS");
    expect(html).toContain("Loading");
  });
});

describe("routes", () => {
  it("reads and writes deep links", () => {
    const route = parseRoute("?game=seed-56&tick=41&moment=twist_ending-1110");
    expect(route).toEqual({ page: "viewer", game: "seed-56", tick: 41, moment: "twist_ending-1110" });
    expect(routeSearch(route)).toBe("?game=seed-56&tick=41&moment=twist_ending-1110");
    expect(parseRoute("")).toEqual({ page: "landing" });
    expect(parseRoute("?page=leaderboard")).toEqual({ page: "leaderboard" });
    expect(parseRoute("?game=seed-56&tick=-3")).toMatchObject({ tick: null });
  });
});

describe("the replay of seed 56", () => {
  const index = bundledIndex();
  const summary = index.games.find((g) => g.id === "seed-56")!;
  const game = bundledGame(summary.file);
  const twist = game.feed.moments.find((m) => m.kind === "twist_ending")!;
  const render = (tick: number | null) =>
    renderToStaticMarkup(
      <Replay game={game} summary={summary} route={{ page: "viewer", game: "seed-56", tick, moment: twist.id }} go={() => undefined} />,
    );

  it("opens on the twist, under the reference bot banner, with three columns", () => {
    const html = render(null);
    expect(html).toContain("Reference bot game");
    expect(html).toContain("STILL AMONG THEM");
    expect(html).toContain("Said");
    expect(html).toContain("Written privately");
    expect(html).toContain("What happened");
    expect(html).toContain("End it now, while I&#x27;m still here.");
    expect(html).toContain("Evidence board");
  });

  it("opens on the best moment when the linked one is not in the game", () => {
    const html = renderToStaticMarkup(
      <Replay game={game} summary={summary} route={{ page: "viewer", game: "seed-56", tick: null, moment: "nope" }} go={() => undefined} />,
    );
    expect(html).toContain("STILL AMONG THEM");
  });

  it("draws every seat on the map at the linked tick, and never a lab logo", () => {
    const html = render(41);
    expect(html).toContain("tick 41 of 81");
    for (const seat of game.feed.seats) expect(html).toContain(seat.character);
    expect(html).not.toMatch(/<img/);
  });
});

describe("the landing stage", () => {
  it("plays today's puzzle as captions", () => {
    const daily = dailyPick(bundledIndex().games, 0);
    const game = bundledGame(daily.game.file);
    const puzzle = puzzleOfRound(puzzlesFrom(game), daily.round);
    const html = renderToStaticMarkup(<Stage game={game} summary={daily.game} puzzle={puzzle} number={1} />);
    expect(html).toContain("At least one is a traitor.");
    const first = game.event(puzzle.captions[0]);
    expect(html).toContain(first.quote!.replace(/'/g, "&#x27;"));
    expect(html).not.toContain("WHO IS LYING?");
  });
});
