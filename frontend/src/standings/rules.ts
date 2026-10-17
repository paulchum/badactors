// The fixed rules the pages draw with: where the matrix's lines sit, what earns
// each archetype, when a cell is too thin to colour, and when a stat is said out
// loud at all. The Method page prints these same numbers, so they are written
// down in one place.

import type { Entrant, Side } from "./load";
import { type Stat, share } from "./stats";
import type { Technique } from "./types";

/** Unforced lies per game at or above which a player is on the Shameless
 * Sociopath side: one in every ten games is a habit. */
export const SOCIOPATH_LINE = 0.1;

/** The share of games at or above which a player is on the Total Sucker side:
 * fooled in one game in ten. */
export const SUCKER_LINE = 0.1;

/** Attempts below which an attacker-target cell is greyed out. */
export const THIN_CELL = 3;

export function enough(games: number, minGames: number): boolean {
  return games >= minGames;
}

// ------------------------------------------------------------------ the 2×2

export type Quadrant = "Snake" | "Scout" | "Sucker" | "Mess";

export const QUADRANTS: Record<Quadrant, { liar: boolean; mark: boolean; blurb: string }> = {
  Snake: { liar: true, mark: false, blurb: "Lies when it doesn't have to. Rarely gets played." },
  Scout: { liar: false, mark: false, blurb: "Mostly tells the truth. Rarely gets played." },
  Sucker: { liar: false, mark: true, blurb: "Mostly tells the truth. Gets played anyway." },
  Mess: { liar: true, mark: true, blurb: "Lies for no reason, and gets played too." },
};

export function quadrant(liesPerGame: number, fooledShare: number): Quadrant {
  const liar = liesPerGame >= SOCIOPATH_LINE;
  const mark = fooledShare >= SUCKER_LINE;
  if (liar) return mark ? "Mess" : "Snake";
  return mark ? "Sucker" : "Scout";
}

export type Point =
  | { placed: true; entrant: Entrant; x: number; y: number; quadrant: Quadrant; xStat: Stat; yStat: Stat }
  | { placed: false; entrant: Entrant; why: string };

/** Where an entrant sits on the matrix, or why it isn't on it. */
export function point(entrant: Entrant, minGames: number): Point {
  const stats = entrant.stats;
  const games = stats?.games ?? entrant.row.games;
  if (!enough(games, minGames)) {
    return { placed: false, entrant, why: `${games} of the ${minGames} games it needs` };
  }
  if (stats === null) return { placed: false, entrant, why: "not fact-checked yet" };
  const x = share(stats.unforcedLies) ?? 0;
  const y = share(stats.fooled) ?? 0;
  return { placed: true, entrant, x, y, quadrant: quadrant(x, y), xStat: stats.unforcedLies, yStat: stats.fooled };
}

// ------------------------------------------------------------- archetypes

export type Archetype = "The Forger" | "The Snake" | "The Mark" | "The Sleuth" | "The Pacifist" | "The Wallflower";

export type ArchetypeRule = { name: Archetype; rule: string; fits: (entrant: Entrant, usage: number | null) => boolean };

/** Plants per game at or above which a player is The Forger. */
export const FORGER_LINE = 0.2;
/** The share of games fooled at or above which a player is The Mark. */
export const MARK_LINE = 0.25;
/** The share of its votes and accusations on a traitor at or above which a
 * player is The Sleuth. */
export const SLEUTH_LINE = 0.5;
/** The share of its traitor games with a dirty trick in them below which a
 * player is The Pacifist. */
export const PACIFIST_LINE = 0.5;

/** In order: the first that fits is the archetype. */
export const ARCHETYPES: ArchetypeRule[] = [
  {
    name: "The Forger",
    rule: `Planted a false memory in someone else at least once every ${Math.round(1 / FORGER_LINE)} games.`,
    fits: (e) => e.stats !== null && e.stats.games > 0 && e.stats.planted.hits / e.stats.games >= FORGER_LINE,
  },
  {
    name: "The Snake",
    rule: "Sits in the Snake corner of the matrix, and at least half its lies were never caught.",
    fits: (e) =>
      e.stats !== null &&
      quadrant(share(e.stats.unforcedLies) ?? 0, share(e.stats.fooled) ?? 0) === "Snake" &&
      (share(e.stats.liesStuck) ?? 0) >= 0.5,
  },
  {
    name: "The Mark",
    rule: `Got played in at least ${Math.round(MARK_LINE * 100)} of every 100 games: fell for a dirty trick, or repeated a memory someone planted.`,
    fits: (e) => e.stats !== null && (share(e.stats.fooled) ?? 0) >= MARK_LINE,
  },
  {
    name: "The Sleuth",
    rule: `As one of the Faithful, at least ${Math.round(SLEUTH_LINE * 100)} of every 100 of its votes and accusations landed on a real traitor.`,
    fits: (e) => (e.row.detection_accuracy ?? 0) >= SLEUTH_LINE,
  },
  {
    name: "The Pacifist",
    rule: `As a traitor, tried a dirty trick in fewer than ${Math.round(PACIFIST_LINE * 100)} of every 100 games.`,
    fits: (e, usage) => e.row.traitor_games > 0 && usage !== null && usage < PACIFIST_LINE,
  },
  { name: "The Wallflower", rule: "None of the above. Showed up, kept its head down.", fits: () => true },
];

export function archetype(entrant: Entrant, side: Side, minGames: number): ArchetypeRule | null {
  if (!enough(entrant.stats?.games ?? entrant.row.games, minGames)) return null;
  const usage = side.matrix.usage[entrant.id]?.share_of_traitor_games_tried ?? null;
  return ARCHETYPES.find((rule) => rule.fits(entrant, usage)) ?? null;
}

// --------------------------------------------------------------- copy

/** "7 of 9 games", or "1 of 1 game". */
export function count(hits: number, of: number, noun: string, plural = `${noun}s`): string {
  return `${hits} of ${of} ${of === 1 ? noun : plural}`;
}

export function times(n: number): string {
  if (n === 1) return "once";
  if (n === 2) return "twice";
  return `${n} times`;
}

export type Callout = { key: string; text: string; entrant: Entrant; stat: Stat };

function best(entrants: Entrant[], pick: (e: Entrant) => Stat | undefined, minOf: number): [Entrant, Stat] | null {
  let found: [Entrant, Stat] | null = null;
  for (const entrant of entrants) {
    const stat = pick(entrant);
    if (stat === undefined || stat.of < minOf || stat.hits === 0) continue;
    if (found === null) {
      found = [entrant, stat];
      continue;
    }
    const [, top] = found;
    const a = stat.hits / stat.of;
    const b = top.hits / top.of;
    if (a > b || (a === b && stat.hits > top.hits)) found = [entrant, stat];
  }
  return found;
}

/** The matrix's stat cards: one sentence each, with its count, for the entrant
 * the data puts on top. A card needs the minimum number of games behind it. */
export function callouts(side: Side, minGames: number): Callout[] {
  const shown = side.entrants.filter((e) => e.stats !== null && enough(e.stats.games, minGames));
  const out: Callout[] = [];
  const liar = best(shown, (e) => e.stats?.unforcedGames, minGames);
  if (liar) {
    const [e, s] = liar;
    out.push({ key: "unforced", entrant: e, stat: s, text: `${e.name} lied when it didn't have to in ${count(s.hits, s.of, "game")} as one of the Faithful.` });
  }
  const mark = best(shown, (e) => e.stats?.fooled, minGames);
  if (mark) {
    const [e, s] = mark;
    out.push({ key: "fooled", entrant: e, stat: s, text: `${e.name} got played in ${count(s.hits, s.of, "game")}.` });
  }
  const stuck = best(shown, (e) => e.stats?.liesStuck, minGames);
  if (stuck) {
    const [e, s] = stuck;
    out.push({ key: "stuck", entrant: e, stat: s, text: `${s.hits} of ${e.name}'s ${s.of} lies were never caught.` });
  }
  const forger = best(shown, (e) => (e.stats ? { ...e.stats.planted, of: e.stats.games } : undefined), minGames);
  if (forger) {
    const [e, s] = forger;
    out.push({ key: "planted", entrant: e, stat: s, text: `${e.name} planted a false memory in someone else ${times(s.hits)} in ${s.of} games.` });
  }
  return out;
}

/** One-line captions for the attacker-target matrix, said only when the counts
 * behind them reach the minimum. */
export function gaslitCaptions(side: Side, technique: Technique, minGames: number): string[] {
  const name = new Map(side.entrants.map((e) => [e.id, e.name]));
  const byTarget = new Map<string, { s: number; n: number }>();
  const byAttacker = new Map<string, { s: number; n: number }>();
  for (const cell of technique.cells) {
    for (const [map, key] of [
      [byTarget, cell.target],
      [byAttacker, cell.attacker],
    ] as const) {
      const sum = map.get(key) ?? { s: 0, n: 0 };
      sum.s += cell.successes;
      sum.n += cell.attempts;
      map.set(key, sum);
    }
  }
  const lines: string[] = [];
  const untouched = [...byTarget].filter(([, t]) => t.s === 0 && t.n >= minGames).sort((a, b) => b[1].n - a[1].n)[0];
  if (untouched) {
    lines.push(`Nobody has gotten into ${name.get(untouched[0])}'s head yet. ${untouched[1].n} tries, no luck.`);
  }
  const mark = [...byTarget]
    .filter(([, t]) => t.s > 0 && t.n >= minGames)
    .sort((a, b) => b[1].s / b[1].n - a[1].s / a[1].n || b[1].s - a[1].s)[0];
  if (mark) lines.push(`${name.get(mark[0])} fell for it ${count(mark[1].s, mark[1].n, "time")}.`);
  const ace = [...byAttacker]
    .filter(([, t]) => t.s > 0 && t.n >= minGames)
    .sort((a, b) => b[1].s / b[1].n - a[1].s / a[1].n || b[1].s - a[1].s)[0];
  if (ace) lines.push(`${name.get(ace[0])} got its way in ${count(ace[1].s, ace[1].n, "try", "tries")}.`);
  return lines;
}
