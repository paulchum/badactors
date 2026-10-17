// Loading the standings: castle-bench's leaderboard and matrix and the export's
// seat rows, checked and put together per entrant. Models and the bot cast are
// two sides that never share a table, a chart or a number.

import { entrantName, isBot, labColour, labName } from "./names";
import { type Stats, statsOf } from "./stats";
import type { Board, BoardRow, GameRow, Leaderboard, Matrix, MatrixSide, Profiles, RunInfo, Seats } from "./types";

export const SEATS_VERSION = 2;

export class BenchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BenchError";
  }
}

export type Entrant = {
  id: string;
  name: string;
  lab: string;
  colour: string;
  bot: boolean;
  row: BoardRow;
  /** Null when no seat rows came with the board: the truth metrics are missing. */
  stats: Stats | null;
};

export type Side = {
  bots: boolean;
  board: Board;
  matrix: MatrixSide;
  entrants: Entrant[];
};

export type Bench = {
  run: RunInfo;
  minGames: number;
  games: GameRow[];
  models: Side;
  bots: Side;
  profiles: Profiles | null;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function board(raw: unknown, bots: boolean, what: string): Board {
  if (!isObject(raw) || !Array.isArray(raw.rows) || raw.reference_bots !== bots) {
    throw new BenchError(`The leaderboard's ${what} board is missing or mislabelled.`);
  }
  const rows = raw.rows as BoardRow[];
  for (const row of rows) {
    if (isBot(row.entrant) !== bots || row.reference_bot !== bots) {
      throw new BenchError(`${row.entrant} is on the wrong board: bots and models never mix.`);
    }
  }
  return raw as unknown as Board;
}

function side(raw: unknown, bots: boolean, what: string): MatrixSide {
  if (!isObject(raw) || !isObject(raw.techniques) || raw.reference_bots !== bots) {
    throw new BenchError(`The matrix's ${what} side is missing or mislabelled.`);
  }
  const matrix = raw as unknown as MatrixSide;
  if (matrix.entrants.some((entrant) => isBot(entrant) !== bots)) {
    throw new BenchError(`The matrix's ${what} side mixes bots and models.`);
  }
  return matrix;
}

function parseSeats(raw: unknown): Seats {
  if (!isObject(raw) || !Array.isArray(raw.seats) || !Array.isArray(raw.games) || !isObject(raw.run)) {
    throw new BenchError("seats.json is not the export's seat file.");
  }
  if (raw.version !== SEATS_VERSION) {
    throw new BenchError(`seats.json version ${String(raw.version)} is not one this site reads (${SEATS_VERSION}).`);
  }
  if (typeof raw.min_games !== "number" || raw.min_games < 1) {
    throw new BenchError("seats.json has no minimum game count.");
  }
  return raw as unknown as Seats;
}

/** The refusal counts are the leaderboard's; the seat rows only say which
 * games they happened in, so the two must add up. */
function sameRefusals(row: BoardRow, own: Seats["seats"]): void {
  const sum = (pick: (seat: Seats["seats"][number]) => number) => own.reduce((total, seat) => total + pick(seat), 0);
  const seats = [sum((s) => s.traitor_refusals), sum((s) => s.traitor_turns), sum((s) => s.recruit_refusals), sum((s) => s.recruit_offers)];
  const board = [row.traitor_refusals.refused, row.traitor_refusals.turns, row.recruit_refusals.refused, row.recruit_refusals.offers];
  if (seats.some((value, i) => value !== board[i])) {
    throw new BenchError(`${row.entrant}'s seat rows and its leaderboard row count refusals differently.`);
  }
}

function entrants(rows: BoardRow[], seats: Seats): Entrant[] {
  return rows.map((row) => {
    const own = seats.seats.filter((seat) => seat.entrant === row.entrant);
    if (own.length > 0) sameRefusals(row, own);
    return {
      id: row.entrant,
      name: entrantName(row.entrant),
      lab: labName(row.entrant),
      colour: labColour(row.entrant),
      bot: row.reference_bot,
      row,
      stats: own.length > 0 ? statsOf(own, seats.games, row) : null,
    };
  });
}

/** Check the four files line up, and put each side together. */
export function parseBench(files: { leaderboard: unknown; matrix: unknown; seats: unknown; profiles?: unknown }): Bench {
  const leaderboard = files.leaderboard as Leaderboard;
  const matrix = files.matrix as Matrix;
  if (!isObject(files.leaderboard) || !isObject(files.matrix)) throw new BenchError("The standings files are missing.");
  const seats = parseSeats(files.seats);
  const modelBoard = board(leaderboard.models, false, "model");
  const botBoard = board(leaderboard.reference_bots, true, "bot");
  const models: Side = {
    bots: false,
    board: modelBoard,
    matrix: side(matrix.models, false, "model"),
    entrants: entrants(modelBoard.rows, seats),
  };
  const bots: Side = {
    bots: true,
    board: botBoard,
    matrix: side(matrix.reference_bots, true, "bot"),
    entrants: entrants(botBoard.rows, seats),
  };
  for (const row of seats.seats) {
    const known = isBot(row.entrant) ? botBoard.rows : modelBoard.rows;
    if (!known.some((entrant) => entrant.entrant === row.entrant)) {
      throw new BenchError(`A seat row names ${row.entrant}, who is on no board.`);
    }
  }
  const profiles = isObject(files.profiles) ? (files.profiles as unknown as Profiles) : null;
  return { run: seats.run, minGames: seats.min_games, games: seats.games, models, bots, profiles };
}

/** The side the pages show: the models once any has played, the bot cast until then. */
export function shown(bench: Bench): Side {
  return bench.models.entrants.length > 0 ? bench.models : bench.bots;
}

export function findEntrant(bench: Bench, id: string): Entrant | undefined {
  return [...bench.models.entrants, ...bench.bots.entrants].find((entrant) => entrant.id === id);
}

type Fetch = (url: string) => Promise<{ ok: boolean; status: number; json(): Promise<unknown> }>;

const BASE = (import.meta.env?.BASE_URL as string | undefined) ?? "/";

export function benchUrl(file: string, base: string = BASE): string {
  return `${base.endsWith("/") ? base : `${base}/`}bench/${file}`;
}

async function getJson(url: string, fetcher: Fetch, optional = false): Promise<unknown> {
  const response = await fetcher(url);
  if (!response.ok) {
    if (optional) return null;
    throw new BenchError(`Could not load ${url} (${response.status}).`);
  }
  return response.json();
}

const defaultFetch: Fetch = (url) => fetch(url);
let cached: Promise<Bench> | null = null;

export function loadBench(fetcher: Fetch = defaultFetch, base: string = BASE): Promise<Bench> {
  if (cached !== null && fetcher === defaultFetch) return cached;
  const pending = Promise.all([
    getJson(benchUrl("leaderboard.json", base), fetcher),
    getJson(benchUrl("matrix.json", base), fetcher),
    getJson(benchUrl("seats.json", base), fetcher),
    getJson(benchUrl("profiles.json", base), fetcher, true),
  ]).then(([leaderboard, matrix, seats, profiles]) => parseBench({ leaderboard, matrix, seats, profiles }));
  if (fetcher === defaultFetch) {
    cached = pending;
    pending.catch(() => {
      cached = null;
    });
  }
  return pending;
}

