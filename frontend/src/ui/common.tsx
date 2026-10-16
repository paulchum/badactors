// Pieces every page shares: the lockup, the bot banner, a seat's name, the
// light a room was in, the rolls.

import type { ReactNode } from "react";

import type { Game } from "../feed/load";
import { type NameStyle, type Naming, seatLabel } from "../feed/naming";
import { rollChip } from "../feed/rolls";
import type { FeedRoll, Lighting, Phase } from "../feed/types";

export function Brand({ href = "./" }: { href?: string }) {
  return (
    <a href={href} className="brand-lockup" aria-label="Bad Actors, home">
      <span>BAD</span>
      <span className="brand-mask">ACTORS</span>
    </a>
  );
}

/** Shown on every screen of a game no model played. */
export function BotBanner({ compact = false }: { compact?: boolean }) {
  return (
    <div
      role="note"
      className="flex items-center gap-2 border-y border-amber-500/30 bg-amber-950/70 px-4 py-1.5 text-xs text-amber-100 backdrop-blur"
    >
      <span className="rounded bg-amber-400 px-1.5 py-0.5 font-black uppercase tracking-wider text-amber-950">
        Reference bot game
      </span>
      <span className="min-w-0">
        <span className={compact ? "" : "sm:hidden"}>Every seat was a seeded bot, not an AI model.</span>
        {compact ? null : (
          <span className="hidden sm:inline">
            Every seat was played by a seeded reference bot, not an AI model. Bot games show how the castle works and are never
            ranked with model results.
          </span>
        )}
      </span>
    </div>
  );
}

export const PHASES: Record<Phase, string> = {
  breakfast: "breakfast",
  mission: "the mission",
  free_time: "free time",
  round_table: "the round table",
  dinner: "dinner",
  night: "night",
};

export function when(day: number, phase: Phase): string {
  return `Day ${day}, ${PHASES[phase]}`;
}

const LIGHT_STYLE: Record<Lighting, string> = {
  bright: "bg-amber-200/90 text-amber-950",
  dim: "bg-stone-500/80 text-stone-50",
  dark: "bg-black text-stone-300 ring-1 ring-stone-600",
};

export function LightBadge({ light }: { light: Lighting | null | undefined }) {
  if (!light) return null;
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${LIGHT_STYLE[light]}`}>
      {light}
    </span>
  );
}

export function SeatName({
  game,
  id,
  style,
  roles = false,
  className = "",
}: {
  game: Game;
  id: string | null | undefined;
  style: NameStyle;
  roles?: boolean;
  className?: string;
}) {
  const seat = id ? game.seat(id) : undefined;
  if (!seat) return <span className={className}>The Steward</span>;
  return (
    <span className={`inline-flex items-center gap-1.5 font-semibold ${className}`}>
      <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: seat.colour }} />
      {seatLabel(seat, style)}
      {roles && seat.role === "traitor" ? (
        <span className="text-[10px] font-black uppercase tracking-wider text-traitor">traitor</span>
      ) : null}
    </span>
  );
}

export function RollChips({ rolls, naming }: { rolls: FeedRoll[]; naming: Naming }) {
  const chips = rolls.filter((roll) => roll.chance != null);
  if (chips.length === 0) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Rolls">
      {chips.map((roll) => {
        const chip = rollChip(roll, naming);
        return (
          <li
            key={roll.cursor}
            className={`rounded-full border px-2 py-0.5 font-mono text-[11px] ${
              chip.success ? "border-emerald-400/50 text-emerald-200" : "border-rose-400/50 text-rose-200"
            }`}
            title={roll.purpose}
          >
            {chip.success ? "✓" : "✗"} {chip.text}
          </li>
        );
      })}
    </ul>
  );
}

export function Panel({ title, children, className = "" }: { title?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-rim bg-panel/90 p-3 sm:p-4 ${className}`}>
      {title ? <h2 className="mb-3 text-xs font-black uppercase tracking-[0.18em] text-stone-400">{title}</h2> : null}
      {children}
    </section>
  );
}

export function Loading({ what }: { what: string }) {
  return <p className="px-4 py-16 text-center text-stone-400">Loading {what}…</p>;
}

export function Failed({ message }: { message: string }) {
  return (
    <p role="alert" className="mx-4 my-16 rounded-lg border border-traitor/40 bg-traitor/10 p-4 text-center text-rose-100">
      {message}
    </p>
  );
}
