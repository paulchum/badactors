// The games bundled in public/games, read from disk for tests. Not used by the site.

import { readFileSync } from "node:fs";

import { Game, parseFeed, parseIndex } from "./load";
import type { GameIndex } from "./types";

const GAMES = new URL("../../public/games/", import.meta.url);

export function bundledIndex(): GameIndex {
  return parseIndex(JSON.parse(readFileSync(new URL("index.json", GAMES), "utf8")));
}

const games = new Map<string, Game>();

export function bundledGame(file: string): Game {
  let game = games.get(file);
  if (game === undefined) {
    game = new Game(parseFeed(JSON.parse(readFileSync(new URL(file, GAMES), "utf8"))));
    games.set(file, game);
  }
  return game;
}
