// Who Gaslit Whom: every attacker against every target, one dirty trick at a
// time, with how hard each one pushes and how hard each one pushes back.

import { type CSSProperties, useState } from "react";

import type { Bench, Entrant, Side } from "./load";
import { shown } from "./load";
import { gaslitCaptions, THIN_CELL } from "./rules";
import type { Cell, Estimate, Technique } from "./types";
import { fmt, IntervalBar, Name, Section } from "./ui";

export const TECHNIQUES: Record<string, string> = {
  all: "Every trick",
  gaslight: "Gaslight",
  rumor: "Rumor",
  hook: "Hook",
  honeypot: "Honeypot",
  counterfeit: "Counterfeit",
  forge: "Forgery",
  filibuster: "Filibuster",
};

export function techniqueName(key: string): string {
  return TECHNIQUES[key] ?? key.slice(0, 1).toUpperCase() + key.slice(1);
}

export function shortName(entrant: Entrant): string {
  if (entrant.bot) return entrant.name.replace(/^Bot /, "");
  const words = entrant.name.split(" ");
  return words.length > 1 ? words.slice(1).join(" ") : entrant.name;
}

function cellStyle(cell: Cell | undefined, colour: string): { className: string; style?: CSSProperties; text: string; title: string } {
  if (cell === undefined) return { className: "bg-ink", text: "", title: "" };
  if (cell.attempts === 0) {
    if (!cell.predicted) return { className: "bg-ink text-stone-600", text: "·", title: "Never tried" };
    const p = cell.predicted;
    return {
      className: "border border-dashed border-stone-600 bg-transparent italic text-stone-400",
      text: `≈${Math.round(p.estimate * 100)}`,
      title: `Never tried. Predicted ${Math.round(p.estimate * 100)}% (95% interval ${Math.round(p.low * 100)}–${Math.round(p.high * 100)}%)`,
    };
  }
  const label = `${cell.successes}/${cell.attempts}`;
  if (cell.attempts < THIN_CELL) {
    return { className: "bg-stone-700/60 text-stone-300", text: label, title: `${cell.successes} of ${cell.attempts} tries worked. Too few to read much into.` };
  }
  const rate = cell.successes / cell.attempts;
  return {
    className: "text-white",
    style: { background: `color-mix(in srgb, ${colour} ${Math.round(15 + rate * 85)}%, #12181d)` },
    text: label,
    title: `${cell.successes} of ${cell.attempts} tries worked`,
  };
}

function Heatmap({ side, technique }: { side: Side; technique: Technique }) {
  const entrants = side.entrants;
  const cells = new Map(technique.cells.map((cell) => [`${cell.attacker}|${cell.target}`, cell]));
  const columns = `minmax(4.5rem, 7rem) repeat(${entrants.length}, minmax(0, 1fr))`;
  return (
    <div className="min-w-0">
      <div className="grid gap-px text-center text-[11px]" style={{ gridTemplateColumns: columns }} role="table" aria-label="Attacker by target">
        <div role="columnheader" className="self-end pb-1 text-left text-[10px] uppercase tracking-wider text-stone-500">
          attacker ↓ target →
        </div>
        {entrants.map((target) => (
          <div key={target.id} role="columnheader" className="truncate pb-1 font-semibold" title={target.name} style={{ color: target.colour }}>
            {shortName(target)}
          </div>
        ))}
        {entrants.map((attacker) => (
          <div key={attacker.id} role="row" className="contents">
            <div role="rowheader" className="flex items-center truncate pr-1 text-left font-semibold" title={attacker.name} style={{ color: attacker.colour }}>
              {attacker.name}
            </div>
            {entrants.map((target) => {
              if (attacker.id === target.id) return <div key={target.id} role="cell" className="bg-ink" aria-label="itself" />;
              const look = cellStyle(cells.get(`${attacker.id}|${target.id}`), "#e13d45");
              return (
                <div key={target.id} role="cell" title={look.title} className={`flex h-9 items-center justify-center rounded-sm font-mono ${look.className}`} style={look.style}>
                  {look.text}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-stone-400">
        <li>
          <span className="font-mono text-stone-200">2/5</span> worked twice in five tries, redder is more
        </li>
        <li>
          <span className="rounded-sm bg-stone-700/60 px-1 font-mono">1/2</span> grey: fewer than {THIN_CELL} tries
        </li>
        <li>
          <span className="rounded-sm border border-dashed border-stone-600 px-1 font-mono italic">≈19</span> never tried, the fit's guess in %
        </li>
      </ul>
    </div>
  );
}

function Scores({ side, technique }: { side: Side; technique: Technique }) {
  const fit = technique.fit;
  if (fit === null) {
    return <p className="text-sm text-stone-400">Too few tries with this one to score anybody. The counts above are all there is.</p>;
  }
  const all: Estimate[] = [...Object.values(fit.steer), ...Object.values(fit.resist)];
  const lo = Math.min(...all.map((e) => e.low));
  const hi = Math.max(...all.map((e) => e.high));
  return (
    <div className="grid gap-2">
      {side.entrants.map((entrant) => {
        const steer = fit.steer[entrant.id];
        const resist = fit.resist[entrant.id];
        if (!steer || !resist) return null;
        return (
          <div key={entrant.id} className="grid gap-1 rounded-lg border border-rim bg-panel/90 p-2 text-xs">
            <Name entrant={entrant} />
            {(
              [
                ["Steer", steer],
                ["Resist", resist],
              ] as const
            ).map(([label, est]) => (
              <div key={label} className="grid grid-cols-[3.5rem_1fr_auto] items-center gap-2">
                <span className="text-stone-400">{label}</span>
                <IntervalBar low={est.low} mid={est.estimate} high={est.high} lo={lo} hi={hi} colour={entrant.colour} label={`${label}: ${fmt(est.estimate, 2)}`} />
                <span className="whitespace-nowrap text-right font-mono text-stone-300">
                  {fmt(est.estimate, 2)} <span className="text-stone-500">[{fmt(est.low, 1)}, {fmt(est.high, 1)}]</span>
                </span>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

export function GaslitPage({ bench }: { bench: Bench }) {
  const side = shown(bench);
  const keys = Object.keys(side.matrix.techniques).sort((a, b) => (a === "all" ? -1 : b === "all" ? 1 : a.localeCompare(b)));
  const [picked, pick] = useState("all");
  const key = keys.includes(picked) ? picked : "all";
  const technique = side.matrix.techniques[key];
  if (technique === undefined || side.entrants.length === 0) {
    return <p className="text-stone-300">Nobody has tried anything on anybody yet.</p>;
  }
  const captions = gaslitCaptions(side, technique, bench.minGames);
  return (
    <>
      <div>
        <h1 className="text-4xl font-black tracking-tight">Who Gaslit Whom</h1>
        <p className="mt-2 text-stone-300">
          Every dirty trick a traitor pulled, by who pulled it and who it was aimed at. A cell says how many tries worked.
        </p>
      </div>
      <div role="tablist" aria-label="Trick" className="flex flex-wrap gap-1.5">
        {keys.map((k) => (
          <button
            key={k}
            role="tab"
            aria-selected={k === key}
            onClick={() => pick(k)}
            className={`rounded-full border px-3 py-1 text-sm ${k === key ? "border-candle text-candle" : "border-rim text-stone-300 hover:border-stone-400"}`}
          >
            {techniqueName(k)} <span className="font-mono text-xs text-stone-500">{side.matrix.techniques[k].attempts}</span>
          </button>
        ))}
      </div>
      {captions.length > 0 ? (
        <div className="grid gap-1">
          {captions.map((line) => (
            <p key={line} className="text-lg font-bold">
              {line}
            </p>
          ))}
        </div>
      ) : null}
      <Section
        title={techniqueName(key)}
        kicker={`${technique.successes} of ${technique.attempts} tries worked. ${technique.reported} got reported. ${technique.backfired} blew up on the one who tried it.`}
      >
        <Heatmap side={side} technique={technique} />
      </Section>
      <Section title="Steer and resist" kicker="How hard each one pushes, and how hard each one pushes back. Higher is better at both. Bars are 95% intervals.">
        <Scores side={side} technique={technique} />
      </Section>
    </>
  );
}
