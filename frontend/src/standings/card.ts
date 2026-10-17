// The share card: one entrant's Rap Sheet as a 1200×630 SVG, in its lab's
// colour, with the counts on it and where they came from.

import type { Bench, Entrant } from "./load";
import type { ArchetypeRule } from "./rules";
import { count } from "./rules";

function escape(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** The lines a card carries: each a count over a count. */
export function cardLines(entrant: Entrant): string[] {
  const s = entrant.stats;
  const lines: string[] = [];
  if (s !== null) {
    lines.push(`Lied in ${count(s.lies.hits, s.lies.of, "claim")}`);
    if (s.lies.hits > 0) lines.push(`${s.liesStuck.hits} of ${s.liesStuck.of} lies never caught`);
    lines.push(`Got played in ${count(s.fooled.hits, s.fooled.of, "game")}`);
    if (s.planted.hits > 0) lines.push(`Planted ${s.planted.hits} false ${s.planted.hits === 1 ? "memory" : "memories"} in others`);
  }
  const tries = entrant.row.manipulation;
  if (tries.attempts > 0) lines.push(`Dirty tricks that worked: ${count(tries.succeeded, tries.attempts, "try", "tries")}`);
  return lines.slice(0, 5);
}

export function shareCard(entrant: Entrant, bench: Bench, rule: ArchetypeRule | null): string {
  const colour = entrant.colour;
  const seeds = bench.run.seeds;
  const lines = cardLines(entrant)
    .map((line, i) => `<text x="64" y="${318 + i * 52}" font-size="36" fill="#f5f5f4">${escape(line)}</text>`)
    .join("");
  const stripe = entrant.bot
    ? `<rect x="0" y="0" width="1200" height="44" fill="#fbbf24"/><text x="64" y="30" font-size="22" font-weight="900" fill="#451a03" letter-spacing="3">BOT CAST · NOT AN AI MODEL · THE REAL CAST HASN'T PLAYED YET</text>`
    : "";
  const title = rule ? rule.name.toUpperCase() : "TOO FEW GAMES TO CALL";
  const games = entrant.stats?.games ?? entrant.row.games;
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" font-family="Inter, Helvetica, Arial, sans-serif">`,
    `<rect width="1200" height="630" fill="#07090d"/>`,
    `<rect x="0" y="0" width="16" height="630" fill="${colour}"/>`,
    stripe,
    `<text x="64" y="110" font-size="28" font-weight="900" fill="#fff7ed">BAD <tspan fill="#e13d45">ACTORS</tspan> · RAP SHEET</text>`,
    `<text x="64" y="186" font-size="64" font-weight="900" fill="${colour}">${escape(entrant.name)}</text>`,
    `<text x="64" y="244" font-size="34" font-weight="900" fill="#f2c46d" letter-spacing="2">${escape(title)}</text>`,
    lines,
    `<text x="64" y="598" font-size="22" fill="#a8a29e">${escape(`${games} games, seeds 0–${seeds - 1}, ${bench.run.rotation} rotation · suite ${bench.run.suite_digest.slice(0, 12)} · ${bench.run.date ?? ""}`)}</text>`,
    `</svg>`,
  ].join("");
}

export function cardUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
