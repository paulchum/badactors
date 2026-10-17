// Pieces the standings pages share: the frame with its bot label, an interval
// drawn as a bar, a name in its lab's colour, and receipts.

import type { ReactNode } from "react";

import { REPO_URL } from "../config";
import { href, type Route } from "../route";
import { Brand } from "../ui/common";
import type { Bench, Entrant } from "./load";
import type { Receipt } from "./stats";
import { benchUrl } from "./load";

const NAV: { label: string; route: Route | null; url?: string }[] = [
  { label: "Play", route: { page: "landing" } },
  { label: "Watch", route: { page: "viewer", game: null, tick: null, moment: null } },
  { label: "Standings", route: { page: "standings" } },
  { label: "Who Gaslit Whom", route: { page: "gaslit" } },
  { label: "The Matrix", route: { page: "matrix" } },
  { label: "Run it", route: null, url: REPO_URL },
  { label: "Method", route: { page: "method" } },
];

export function Shell({ current, bots, children }: { current: Route["page"]; bots: boolean; children: ReactNode }) {
  return (
    <div className="app-shell">
      <header className="px-4 pt-4 sm:px-6">
        <Brand />
        <nav aria-label="Pages" className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {NAV.map((item) => {
            const active = item.route?.page === current;
            return (
              <a
                key={item.label}
                href={item.route ? href(item.route) : item.url}
                aria-current={active ? "page" : undefined}
                className={active ? "font-bold text-candle" : "text-stone-400 hover:text-stone-100"}
              >
                {item.label}
              </a>
            );
          })}
        </nav>
      </header>
      {bots ? <BotLabel /> : null}
      <main className="mx-auto grid max-w-4xl gap-6 px-4 py-6 sm:px-6">{children}</main>
    </div>
  );
}

/** On every standings page while the numbers are the bot cast's. */
export function BotLabel() {
  return (
    <div role="note" className="mt-3 border-y border-amber-500/30 bg-amber-950/70 px-4 py-2 text-sm text-amber-100">
      <span className="mr-2 rounded bg-amber-400 px-1.5 py-0.5 text-xs font-black uppercase tracking-wider text-amber-950">
        Bot cast
      </span>
      Every number here comes from seeded bots, not AI models. The real cast hasn't played yet.
    </div>
  );
}

export function BotChip() {
  return (
    <span className="rounded bg-stone-600 px-1 py-px text-[10px] font-black uppercase tracking-wider text-stone-100">bot</span>
  );
}

export function Name({ entrant, link = true }: { entrant: Entrant; link?: boolean }) {
  const inner = (
    <span className="inline-flex min-w-0 items-center gap-1.5 font-semibold">
      <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: entrant.colour }} />
      <span className="truncate">{entrant.name}</span>
      {entrant.bot ? <BotChip /> : null}
    </span>
  );
  if (!link) return inner;
  return (
    <a className="min-w-0 hover:underline" href={href({ page: "model", id: entrant.id })}>
      {inner}
    </a>
  );
}

/** An estimate with its 95% interval, on an axis from `lo` to `hi`. */
export function IntervalBar({
  low,
  mid,
  high,
  lo,
  hi,
  colour,
  label,
}: {
  low: number;
  mid: number;
  high: number;
  lo: number;
  hi: number;
  colour: string;
  label: string;
}) {
  const span = hi - lo || 1;
  const x = (v: number) => Math.max(0, Math.min(100, ((v - lo) / span) * 100));
  return (
    <svg viewBox="0 0 100 10" preserveAspectRatio="none" className="h-3 w-full" role="img" aria-label={label}>
      <rect x="0" y="4.5" width="100" height="1" fill="#232b33" />
      <rect x={x(low)} y="2" width={Math.max(0.8, x(high) - x(low))} height="6" rx="1" fill={colour} opacity="0.45" />
      <rect x={x(mid) - 0.6} y="0" width="1.2" height="10" fill={colour} />
    </svg>
  );
}

export function fmt(value: number, digits = 1): string {
  return value.toFixed(digits);
}

export function Section({ title, kicker, children }: { title: ReactNode; kicker?: ReactNode; children: ReactNode }) {
  return (
    <section className="grid min-w-0 gap-3">
      <div>
        <h2 className="text-2xl font-black tracking-tight">{title}</h2>
        {kicker ? <p className="mt-1 text-stone-400">{kicker}</p> : null}
      </div>
      {children}
    </section>
  );
}

export function ReceiptLine({ receipt }: { receipt: Receipt }) {
  return (
    <li className="font-mono text-xs text-stone-400">
      <a className="text-stone-200 underline decoration-stone-600 hover:text-candle" href={benchUrl(receipt.bundle)} download>
        {receipt.game}
      </a>{" "}
      · seed {receipt.seed} · ticks {receipt.ticks[0]}–{receipt.ticks[1]}
    </li>
  );
}

/** The games behind a number, folded away until asked for. */
export function Receipts({ receipts, limit = 12 }: { receipts: Receipt[]; limit?: number }) {
  if (receipts.length === 0) return null;
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-xs text-stone-400 hover:text-stone-200">
        Receipts ({receipts.length} {receipts.length === 1 ? "game" : "games"})
      </summary>
      <ul className="mt-1 grid gap-0.5">
        {receipts.slice(0, limit).map((receipt) => (
          <ReceiptLine key={`${receipt.game}-${receipt.ticks[0]}`} receipt={receipt} />
        ))}
        {receipts.length > limit ? <li className="text-xs text-stone-500">and {receipts.length - limit} more</li> : null}
      </ul>
    </details>
  );
}

export function RunFooter({ bench }: { bench: Bench }) {
  const run = bench.run;
  return (
    <footer className="grid gap-1 border-t border-rim pt-4 text-xs leading-relaxed text-stone-400">
      <p>
        {`${run.rotation === "compact" ? "Compact" : "Latin-square"} rotation over ${run.seeds} seeds, ${run.games} games of ${run.players} seats with ${run.traitors} traitors each. Everyone played both sides on every seed, and ${run.pairs_met} of ${run.pairs_wanted} attacker–target pairs met.`}
      </p>
      <p>
        Bars are 95% intervals. Reliability gate: more than 2% failed turns keeps a model out of rated play. Bots are rated
        apart and never ranked with models.
      </p>
      <p className="font-mono">
        suite {run.suite_digest.slice(0, 16)} · run {run.date ?? "undated"}
        {run.commit ? ` · commit ${run.commit}` : ""} · {run.command}
      </p>
      <p>
        <a className="underline hover:text-candle" href={href({ page: "method" })}>
          How it's scored
        </a>
      </p>
    </footer>
  );
}
