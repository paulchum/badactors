// Loading the index and one game's feed into React state.

import { useEffect, useState } from "react";

import { Game, loadFeed, loadIndex } from "../feed/load";
import type { GameIndex, GameSummary } from "../feed/types";

export type Loaded<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; value: T };

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useIndex(): Loaded<GameIndex> {
  const [index, setIndex] = useState<Loaded<GameIndex>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    loadIndex()
      .then((value) => live && setIndex({ state: "ready", value }))
      .catch((error) => live && setIndex({ state: "error", message: message(error) }));
    return () => {
      live = false;
    };
  }, []);
  return index;
}

export function useGame(summary: GameSummary | null): Loaded<Game> {
  const [game, setGame] = useState<Loaded<Game>>({ state: "loading" });
  const file = summary?.file ?? null;
  useEffect(() => {
    if (file === null) return;
    let live = true;
    setGame({ state: "loading" });
    loadFeed({ file })
      .then((feed) => live && setGame({ state: "ready", value: new Game(feed) }))
      .catch((error) => live && setGame({ state: "error", message: message(error) }));
    return () => {
      live = false;
    };
  }, [file]);
  return game;
}
