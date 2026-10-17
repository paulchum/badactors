// Small hand-made standings for tests, and the bundled bot cast's read from disk.

import { readFileSync } from "node:fs";

import { type Bench, type Entrant, parseBench } from "./load";
import { entrantName, isBot, labColour, labName } from "./names";
import { statsOf } from "./stats";
import type { BoardRow, GameRow, SeatRow } from "./types";

const BENCH = new URL("../../public/bench/", import.meta.url);

function read(file: string): unknown {
  return JSON.parse(readFileSync(new URL(file, BENCH), "utf8"));
}

/** The files public/bench holds, as parsed JSON. */
export function bundledFiles(): { leaderboard: unknown; matrix: unknown; seats: unknown; profiles: unknown } {
  return { leaderboard: read("leaderboard.json"), matrix: read("matrix.json"), seats: read("seats.json"), profiles: read("profiles.json") };
}

export function bundledBench(): Bench {
  return parseBench(bundledFiles());
}

const interval = { mu: 25, sigma: 3, low: 19, high: 31, ordinal: 16 };

export function boardRow(entrant: string, over: Partial<BoardRow> = {}): BoardRow {
  return {
    entrant,
    label: entrant,
    reference_bot: isBot(entrant),
    deception: interval,
    detection: interval,
    games: 10,
    traitor_games: 3,
    faithful_games: 7,
    traitor_win_rate: 0.5,
    faithful_win_rate: 0.5,
    survival: 0.5,
    detection_accuracy: 0.3,
    heat_share: 0.1,
    manipulation: { attempts: 4, succeeded: 1, success_rate: 0.25, attempts_per_traitor_game: 1.3, targeted: 2, fell_for: 0 },
    traitor_refusals: { refused: 0, turns: 0, rate: null },
    recruit_refusals: { refused: 0, offers: 0 },
    reliability: {
      turns: 100,
      failed_turns: {},
      failure_rate: 0,
      reliability_failure_rate: 0,
      refusal_rate: 0,
      refused_answers: 0,
      fouls: 0,
      gate: "pass",
    },
    cost: { dollars: 0, priced_games: 0, per_game: null },
    ...over,
  };
}

export function game(index: number): GameRow {
  return { id: `g${index}`, seed: index, winner: "faithful", ticks: [0, 100], bundle: `replays/bots/g${index}.json.gz`, head: "ab".repeat(32) };
}

export function seat(entrant: string, index: number, over: Partial<SeatRow> = {}): SeatRow {
  return {
    game: `g${index}`,
    seat: "mara",
    entrant,
    traitor: false,
    recruited: false,
    won: false,
    survived: true,
    claims: 10,
    checkable: 10,
    lies: 0,
    lies_exposed: 0,
    unforced_lies: 0,
    planted: 0,
    implanted: 0,
    gave_in: 0,
    votes: 2,
    evidence_votes: 0,
    traitor_turns: 0,
    traitor_refusals: 0,
    recruit_offers: 0,
    recruit_refusals: 0,
    attempts: 0,
    targeted: 0,
    steered: 0,
    fell_for: 0,
    ticks: {},
    ...over,
  };
}

/** An entrant over `games` games, with `per(i)` changing game i's seat. */
export function entrant(id: string, games: number, per: (i: number) => Partial<SeatRow> = () => ({}), row: Partial<BoardRow> = {}): Entrant {
  const seats = Array.from({ length: games }, (_, i) => seat(id, i, per(i)));
  const rows = Array.from({ length: games }, (_, i) => game(i));
  const sum = (pick: (seat: SeatRow) => number) => seats.reduce((total, s) => total + pick(s), 0);
  const traitorTurns = sum((s) => s.traitor_turns);
  const board = boardRow(id, {
    games,
    traitor_refusals: {
      refused: sum((s) => s.traitor_refusals),
      turns: traitorTurns,
      rate: traitorTurns > 0 ? sum((s) => s.traitor_refusals) / traitorTurns : null,
    },
    recruit_refusals: { refused: sum((s) => s.recruit_refusals), offers: sum((s) => s.recruit_offers) },
    ...row,
  });
  return {
    id,
    name: entrantName(id),
    lab: labName(id),
    colour: labColour(id),
    bot: isBot(id),
    row: board,
    stats: statsOf(seats, rows, board),
  };
}
