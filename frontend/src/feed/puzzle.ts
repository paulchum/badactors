// "Who is lying?": the landing game. A puzzle is one round table where three
// players accuse each other and exactly one of them is a traitor accusing one
// of the Faithful, a claim it knew was false. The roles say who lied (as
// castle/moments.py reads a knowing falsehood until the fact-checker rules);
// the votes say how many at the table caught it.

import type { Game } from "./load";
import { type Table, tables } from "./story";
import type { FeedEvent, GameSummary } from "./types";

export interface Suspect {
  id: string;
  /** The entry holding the words they are heard saying. */
  words: number;
  /** An entry holding their own private line about it, if any. */
  line: number | null;
  /** Their vote at this table, if they cast one. */
  vote: string | null;
  traitor: boolean;
}

export interface Puzzle {
  id: string;
  day: number;
  table: Table;
  liar: string;
  /** The suspects in the order they spoke. */
  suspects: Suspect[];
  /** The entries played as captions, in order. */
  captions: number[];
  /** The entries that show the liar is a traitor: how they became one, and the
   * last thing they did in secret before lying. */
  truth: number[];
  /** The Faithful who voted at this table: the players who could be fooled. */
  voters: string[];
  /** Those of them who voted to banish the liar. */
  caught: string[];
  quality: number;
}

const KILLING = new Set(["poison", "nominate", "recruit_offer"]);
const COVERT = new Set([
  "forge",
  "counterfeit",
  "honeypot",
  "tamper",
  "plant",
  "steal",
  "hook",
  "gaslight",
  "rumor",
  "blackmail",
  "clean_up",
  "disguise",
  "sabotage",
]);

function covertAct(game: Game, player: string, before: number): FeedEvent | null {
  const earlier = game.feed.events.slice(0, before - 1).reverse();
  for (const kinds of [KILLING, COVERT]) {
    for (const event of earlier) {
      if (event.actor !== player || !event.room) continue;
      if (kinds.has(event.type) || kinds.has(event.payload.tool ?? "")) return event;
    }
  }
  return null;
}

function hasLine(event: FeedEvent | undefined): boolean {
  return Boolean(event?.private.diary || event?.private.reason);
}

function puzzleAt(game: Game, table: Table): Puzzle | null {
  const window = game.feed.events.slice(table.opened, table.closed - 1);
  const said = (pid: string, types: string[]) =>
    types
      .map((type) => window.find((e) => e.actor === pid && e.type === type && e.quote))
      .find((e) => e !== undefined);
  const accusations = window.filter((e) => e.type === "accuse" && e.actor && e.quote);
  const faithful = (pid: string, seq: number) => !game.traitorAt(pid, seq);
  const lies = accusations.filter(
    (e) => game.traitorAt(e.actor!, e.seq) && e.payload.target && faithful(e.payload.target, e.seq),
  );
  const votes = new Map(table.votes.map((vote) => [vote.voter, vote.target]));
  const votedAt = (pid: string) => window.find((e) => e.type === "vote" && e.actor === pid);
  let best: Puzzle | null = null;
  const tried = new Set<string>();
  for (const lie of lies) {
    const liar = lie.actor!;
    if (tried.has(liar)) continue;
    tried.add(liar);
    const accused = lie.payload.target;
    const others: FeedEvent[] = [];
    const take = (event: FeedEvent | undefined) => {
      if (!event || others.length >= 2) return;
      const pid = event.actor!;
      if (pid === liar || others.some((o) => o.actor === pid) || !faithful(pid, event.seq)) return;
      others.push(event);
    };
    take(said(accused, ["accuse", "defend"]));
    for (const e of accusations) if ([liar, accused].includes(e.payload.target)) take(e);
    for (const e of accusations) take(e);
    if (others.length < 2) continue;
    const words = [lie, ...others].sort((a, b) => a.seq - b.seq);
    const suspects: Suspect[] = words.map((e) => {
      const pid = e.actor!;
      const ballot = votedAt(pid);
      return {
        id: pid,
        words: e.seq,
        line: hasLine(e) ? e.seq : hasLine(ballot) ? ballot!.seq : null,
        vote: votes.get(pid) ?? null,
        traitor: pid === liar,
      };
    });
    const denial = said(liar, ["defend"]);
    const captions = words.map((e) => e.seq);
    if (denial && denial.seq > lie.seq) captions.push(denial.seq);
    captions.sort((a, b) => a - b);
    const made = game.seat(liar)?.traitor_from;
    const secret = covertAct(game, liar, lie.seq);
    const truth = [...new Set([made, secret?.seq].filter((seq): seq is number => seq != null))].sort((a, b) => a - b);
    const voters = table.votes
      .filter((vote) => vote.voter !== liar && !game.traitorAt(vote.voter, table.closed))
      .map((vote) => vote.voter);
    const caught = voters.filter((voter) => votes.get(voter) === liar);
    const fooled = voters.length - caught.length;
    const quality =
      (lie.private.diary ? 2 : 1) * (1 + fooled) * (table.role === "faithful" ? 2 : 1) * (accused === others[0].actor ? 2 : 1);
    const puzzle: Puzzle = {
      id: `${table.day}-${table.opened}`,
      day: table.day,
      table,
      liar,
      suspects,
      captions,
      truth,
      voters,
      caught,
      quality,
    };
    if (best === null || puzzle.quality > best.quality) best = puzzle;
  }
  return best;
}

/** Every puzzle a game holds, one per round table at most, best first. */
export function puzzlesFrom(game: Game): Puzzle[] {
  return tables(game)
    .map((table) => puzzleAt(game, table))
    .filter((puzzle): puzzle is Puzzle => puzzle !== null)
    .sort((a, b) => b.quality - a.quality || a.day - b.day);
}

export interface Result {
  correct: boolean;
  pick: string;
  liar: string;
  /** Faithful at the table who voted for the liar. */
  caught: number;
  /** Faithful who voted at the table. */
  of: number;
}

export function score(puzzle: Puzzle, pick: string): Result {
  if (!puzzle.suspects.some((suspect) => suspect.id === pick)) {
    throw new Error(`${pick} is not a suspect in this puzzle.`);
  }
  return {
    correct: pick === puzzle.liar,
    pick,
    liar: puzzle.liar,
    caught: puzzle.caught.length,
    of: puzzle.voters.length,
  };
}

// --------------------------------------------------------------- the daily

/** The first puzzle's date. Puzzle #1 is this day's. */
export const EPOCH = Date.UTC(2026, 9, 16);
const DAY = 86_400_000;

/** Days since the first puzzle, by the calendar date where the player is. */
export function dayNumber(date: Date): number {
  return Math.floor((Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) - EPOCH) / DAY);
}

/** Which bundled game a day's puzzle comes from, and which of its puzzles:
 * the games take turns, and each game walks through its puzzles, best first. */
export function dailyPick(games: GameSummary[], day: number): { game: GameSummary; round: number } {
  if (games.length === 0) throw new Error("No bundled games.");
  const n = games.length;
  const at = ((day % n) + n) % n;
  return { game: games[at], round: Math.floor(day / n) };
}

export function puzzleOfRound(puzzles: Puzzle[], round: number): Puzzle {
  if (puzzles.length === 0) throw new Error("This game has no puzzle.");
  const n = puzzles.length;
  return puzzles[((round % n) + n) % n];
}

/** The text a player shares: how they did and how the table did, without
 * saying who lied. */
export function shareGrid(puzzle: Puzzle, result: Result, number: number, botGame: boolean): string {
  const table = puzzle.voters.map((voter) => (puzzle.caught.includes(voter) ? "🟩" : "🟥")).join("");
  return [
    `Bad Actors · Who is lying? #${number}`,
    `${result.correct ? "🟩" : "🟥"} me: ${result.correct ? "caught the liar" : "fooled"}`,
    `${table} the table: ${result.caught} of ${result.of} caught it`,
    ...(botGame ? ["Played by reference bots"] : []),
  ].join("\n");
}
