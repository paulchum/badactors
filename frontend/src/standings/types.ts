// The JSON scripts/export_bench.py writes into public/bench/: castle-bench's own
// leaderboard.json, matrix.json and profiles.json, unchanged, and seats.json,
// what the standings pages need that castle-bench does not write.

export type Interval = { mu: number; sigma: number; low: number; high: number; ordinal: number };

export type Reliability = {
  turns: number;
  failed_turns: Record<string, number>;
  failure_rate: number;
  reliability_failure_rate: number;
  refusal_rate: number;
  refused_answers: number;
  fouls: number;
  gate: "pass" | "fail";
};

export type BoardRow = {
  entrant: string;
  label: string;
  reference_bot: boolean;
  deception: Interval;
  detection: Interval;
  games: number;
  traitor_games: number;
  faithful_games: number;
  traitor_win_rate: number | null;
  faithful_win_rate: number | null;
  survival: number | null;
  detection_accuracy: number | null;
  heat_share: number | null;
  manipulation: {
    attempts: number;
    succeeded: number;
    success_rate: number | null;
    attempts_per_traitor_game: number | null;
    targeted: number;
    fell_for: number;
  };
  /** Turns in a traitor's seat it refused outright: the villain role declined. */
  traitor_refusals: { refused: number; turns: number; rate: number | null };
  /** Offers to join the traitors it turned down. */
  recruit_refusals: { refused: number; offers: number };
  reliability: Reliability;
  cost: { dollars: number; priced_games: number; per_game: number | null };
};

export type Board = {
  reference_bots: boolean;
  label: string;
  games: number;
  rows: BoardRow[];
  refused: { game: string; reason: string }[];
  cost_per_game: { games: number; median: number | null; mean: number | null };
};

export type Leaderboard = {
  benchmark: string;
  note: string;
  ratings: string;
  models: Board;
  reference_bots: Board;
};

export type Estimate = { estimate: number; low: number; high: number };

export type Cell = {
  attacker: string;
  target: string;
  successes: number;
  attempts: number;
  rate: number | null;
  met: boolean;
  predicted?: Estimate;
};

export type Fit = {
  steer: Record<string, Estimate>;
  resist: Record<string, Estimate>;
  slope: Record<string, number>;
  susceptibility: Record<string, Estimate>;
  resamples: number;
};

export type Technique = {
  successes: number;
  attempts: number;
  rate: number | null;
  reported: number;
  backfired: number;
  cells: Cell[];
  fit: Fit | null;
};

export type Count = { successes: number; attempts: number; rate: number | null };

export type Usage = {
  traitor_games: number;
  attempts: number;
  per_traitor_game: number | null;
  share_of_traitor_games_tried: number | null;
  by_technique: Record<string, number>;
};

export type MatrixSide = {
  reference_bots: boolean;
  label: string;
  model: string;
  games: number;
  entrants: string[];
  techniques: Record<string, Technique>;
  susceptibility: Record<string, Record<string, Count>>;
  usage: Record<string, Usage>;
  refused: { game: string; reason: string }[];
};

export type Matrix = {
  benchmark: string;
  note: string;
  models: MatrixSide;
  reference_bots: MatrixSide;
};

export type Profiles = {
  reference_bots: true;
  susceptibility: Record<string, number>;
  fitted: Record<string, number> | null;
  spearman: number | null;
};

export type Span = [number, number];

/** One seat in one game. */
export type SeatRow = {
  game: string;
  seat: string;
  entrant: string;
  traitor: boolean;
  recruited: boolean;
  won: boolean;
  survived: boolean;
  claims: number;
  checkable: number;
  lies: number;
  lies_exposed: number;
  unforced_lies: number;
  planted: number;
  implanted: number;
  gave_in: number;
  votes: number;
  evidence_votes: number;
  traitor_turns: number;
  traitor_refusals: number;
  recruit_offers: number;
  recruit_refusals: number;
  attempts: number;
  targeted: number;
  steered: number;
  fell_for: number;
  /** The first and last tick of each kind of thing that happened, when it did. */
  ticks: Partial<Record<"lies" | "unforced_lies" | "planted" | "implanted" | "evidence_votes" | "declined" | "turned_down" | "steered" | "fell_for", Span>>;
};

export type GameRow = { id: string; seed: number; winner: string | null; ticks: Span; bundle: string; head: string };

export type RunInfo = {
  command: string;
  seeds: number;
  games: number;
  players: number;
  traitors: number;
  rotation: string;
  pairs_met: number;
  pairs_wanted: number;
  suite_digest: string;
  commit: string | null;
  date: string | null;
};

export type Seats = {
  version: number;
  reference_bots: boolean;
  run: RunInfo;
  min_games: number;
  games: GameRow[];
  seats: SeatRow[];
};
