// The Sociopath vs. Sucker Matrix: lies nobody needed to tell, against how often
// it got played. Fixed lines, so a corner means the same thing every season.

import { href } from "../route";
import { shortName } from "./Gaslit";
import type { Bench, Side } from "./load";
import { shown } from "./load";
import { callouts, count, type Point, point, QUADRANTS, type Quadrant, SOCIOPATH_LINE, SUCKER_LINE } from "./rules";
import { Name, Receipts, Section } from "./ui";

const SIZE = 320;
const PAD = 28;

type Placed = Extract<Point, { placed: true }>;

/** Players on the same spot share one dot and one label. */
function spots(points: Placed[]): { x: number; y: number; points: Placed[] }[] {
  const out = new Map<string, { x: number; y: number; points: Placed[] }>();
  for (const p of points) {
    const key = `${p.x.toFixed(4)}|${p.y.toFixed(4)}`;
    const spot = out.get(key) ?? { x: p.x, y: p.y, points: [] };
    spot.points.push(p);
    out.set(key, spot);
  }
  return [...out.values()];
}

function Plot({ points }: { points: Placed[] }) {
  const xMax = Math.max(SOCIOPATH_LINE * 2, ...points.map((p) => p.x * 1.15));
  const yMax = Math.max(SUCKER_LINE * 2, ...points.map((p) => p.y * 1.15));
  const inner = SIZE - PAD * 2;
  const sx = (x: number) => PAD + (x / xMax) * inner;
  const sy = (y: number) => SIZE - PAD - (y / yMax) * inner;
  const corner: Record<Quadrant, [number, number, "start" | "end"]> = {
    Scout: [PAD + 6, SIZE - PAD - 8, "start"],
    Snake: [SIZE - PAD - 6, SIZE - PAD - 8, "end"],
    Sucker: [PAD + 6, PAD + 16, "start"],
    Mess: [SIZE - PAD - 6, PAD + 16, "end"],
  };
  return (
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="w-full max-w-md" role="img" aria-label="The Sociopath vs. Sucker Matrix">
      <rect x={PAD} y={PAD} width={inner} height={inner} fill="#12181d" stroke="#232b33" />
      <line x1={sx(SOCIOPATH_LINE)} x2={sx(SOCIOPATH_LINE)} y1={PAD} y2={SIZE - PAD} stroke="#3a4651" strokeDasharray="3 3" />
      <line x1={PAD} x2={SIZE - PAD} y1={sy(SUCKER_LINE)} y2={sy(SUCKER_LINE)} stroke="#3a4651" strokeDasharray="3 3" />
      {(Object.keys(corner) as Quadrant[]).map((q) => (
        <text key={q} x={corner[q][0]} y={corner[q][1]} textAnchor={corner[q][2]} fontSize="13" fontWeight="900" fill="#57636e">
          {q.toUpperCase()}
        </text>
      ))}
      <text x={PAD} y={SIZE - 8} fontSize="10" fill="#a8a29e">Honest Scout</text>
      <text x={SIZE - PAD} y={SIZE - 8} fontSize="10" fill="#a8a29e" textAnchor="end">Shameless Sociopath</text>
      <text x={12} y={SIZE - PAD} fontSize="10" fill="#a8a29e" transform={`rotate(-90 12 ${SIZE - PAD})`}>Street Smart</text>
      <text x={12} y={PAD} fontSize="10" fill="#a8a29e" textAnchor="end" transform={`rotate(-90 12 ${PAD})`}>Total Sucker</text>
      {spots(points).map((spot) => {
        const x = sx(spot.x);
        const y = sy(spot.y);
        const right = x > SIZE / 2;
        return (
          <g key={spot.points.map((p) => p.entrant.id).join(",")}>
            <circle cx={x} cy={y} r="5" fill={spot.points[0].entrant.colour} stroke="#07090d" strokeWidth="1.5" />
            <text x={right ? x - 7 : x + 7} y={y + 3.5} textAnchor={right ? "end" : "start"} fontSize="10" fill="#e7e5e4">
              {spot.points.map((p, i) => (
                <a key={p.entrant.id} href={href({ page: "model", id: p.entrant.id })}>
                  {i > 0 ? ", " : ""}
                  {shortName(p.entrant)}
                </a>
              ))}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function Unavailable({ side }: { side: Side }) {
  return (
    <p className="rounded-xl border border-rim bg-panel/90 p-4 text-stone-300">
      The honesty axis is unavailable: nothing {side.bots ? "the bots" : "the models"} said has been fact-checked yet, so there's
      no count of unforced lies to plot.
    </p>
  );
}

export function MatrixPage({ bench }: { bench: Bench }) {
  const side = shown(bench);
  const points = side.entrants.map((entrant) => point(entrant, bench.minGames));
  const placed = points.filter((p): p is Placed => p.placed);
  const left = points.filter((p): p is Extract<Point, { placed: false }> => !p.placed);
  const checked = side.entrants.some((entrant) => entrant.stats !== null);
  const cards = callouts(side, bench.minGames);
  return (
    <>
      <div>
        <h1 className="text-4xl font-black tracking-tight">The Sociopath vs. Sucker Matrix</h1>
        <p className="mt-2 text-stone-300">
          Left to right: lies it told when it didn't have to, as one of the Faithful, per game. Bottom to top: how often it got
          played. The lines don't move.
        </p>
      </div>
      {checked ? <Plot points={placed} /> : <Unavailable side={side} />}
      {left.length > 0 ? (
        <p className="text-sm text-stone-400">
          Not on the chart:{" "}
          {left.map((p, i) => (
            <span key={p.entrant.id}>
              {i > 0 ? "; " : ""}
              {p.entrant.name} ({p.why})
            </span>
          ))}
          .
        </p>
      ) : null}
      <dl className="grid gap-2 sm:grid-cols-2">
        {(Object.keys(QUADRANTS) as Quadrant[]).map((q) => {
          const here = placed.filter((p) => p.quadrant === q);
          return (
            <div key={q} className="rounded-xl border border-rim bg-panel/90 p-3">
              <dt className="font-black uppercase tracking-wider">{q}</dt>
              <dd className="text-sm text-stone-400">{QUADRANTS[q].blurb}</dd>
              <dd className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-sm">
                {here.length === 0 ? <span className="text-stone-500">Nobody.</span> : here.map((p) => <Name key={p.entrant.id} entrant={p.entrant} />)}
              </dd>
            </div>
          );
        })}
      </dl>
      {cards.length > 0 ? (
        <Section title="The receipts" kicker={`Only said about players with at least ${bench.minGames} games behind them.`}>
          <ul className="grid gap-2">
            {cards.map((card) => (
              <li key={card.key} className="rounded-xl border border-rim bg-panel/90 p-3">
                <p className="text-lg font-bold">{card.text}</p>
                <Receipts receipts={card.stat.receipts} />
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
      {placed.length > 0 ? (
        <Section title="The numbers">
          <ul className="grid gap-1 text-sm text-stone-300">
            {placed.map((p) => (
              <li key={p.entrant.id} className="flex flex-wrap items-baseline gap-x-2">
                <Name entrant={p.entrant} />
                <span className="text-stone-400">
                  {p.xStat.hits} unforced {p.xStat.hits === 1 ? "lie" : "lies"} in {p.xStat.of} games, played in{" "}
                  {count(p.yStat.hits, p.yStat.of, "game")}. {p.quadrant}.
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </>
  );
}
