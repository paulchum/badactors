// @vitest-environment happy-dom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";

import { Game, parseFeed, parseIndex } from "../feed/load";
import { dailyPick, puzzleOfRound, puzzlesFrom } from "../feed/puzzle";
import { Stage } from "./Landing";

it("shows every statement before asking for a vote when the captions are skipped", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  window.localStorage.clear();
  const games = resolve("public/games");
  const index = parseIndex(JSON.parse(readFileSync(resolve(games, "index.json"), "utf8")));
  const daily = dailyPick(index.games, 0);
  const game = new Game(parseFeed(JSON.parse(readFileSync(resolve(games, daily.game.file), "utf8"))));
  const puzzle = puzzleOfRound(puzzlesFrom(game), daily.round);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => {
      root.render(<Stage game={game} summary={daily.game} puzzle={puzzle} number={1} />);
    });
    expect(container.querySelectorAll("ol li")).toHaveLength(1);
    const skip = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Skip to the vote");
    expect(skip).toBeDefined();
    await act(async () => skip!.click());

    expect(container.querySelector("#ask")?.textContent).toBe("WHO'S LYING?");
    expect(container.querySelectorAll("ol li")).toHaveLength(puzzle.captions.length);
    for (const seq of puzzle.captions) {
      expect(container.textContent).toContain(game.event(seq).quote);
    }
    expect(container.querySelectorAll("#ask + div button")).toHaveLength(puzzle.suspects.length);
  } finally {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  }
});
