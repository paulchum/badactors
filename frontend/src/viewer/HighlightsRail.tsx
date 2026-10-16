// The game's moments as a strip to jump between: the twist first, then the
// best of each kind, then the rest, as castle-game --highlights orders them.

import type { FeedMoment } from "../feed/types";
import { type NameStyle, headline } from "../feed/naming";

export function railOrder(moments: FeedMoment[]): FeedMoment[] {
  const seen = new Map<string, number>();
  const ranked = moments.map((moment, rank) => {
    const count = (seen.get(moment.kind) ?? 0) + 1;
    seen.set(moment.kind, count);
    return { moment, rank, twist: moment.kind === "twist_ending", spare: count > 2 };
  });
  ranked.sort((a, b) => Number(b.twist) - Number(a.twist) || Number(a.spare) - Number(b.spare) || a.rank - b.rank);
  return ranked.map((entry) => entry.moment);
}

export function HighlightsRail({
  moments,
  selected,
  style,
  onSelect,
}: {
  moments: FeedMoment[];
  selected: string | null;
  style: NameStyle;
  onSelect: (moment: FeedMoment) => void;
}) {
  return (
    <nav aria-label="Highlights" className="-mx-4 min-w-0 sm:mx-0">
      <ol className="no-scrollbar flex snap-x gap-2 overflow-x-auto px-4 pb-1 sm:px-0">
        {railOrder(moments).map((moment) => {
          const active = moment.id === selected;
          const twist = moment.kind === "twist_ending";
          return (
            <li key={moment.id} className="snap-start">
              <button
                type="button"
                onClick={() => onSelect(moment)}
                aria-current={active ? "true" : undefined}
                className={`flex h-full w-60 flex-col gap-1.5 rounded-lg border p-2.5 text-left transition ${
                  active
                    ? "border-candle bg-candle/10"
                    : twist
                      ? "border-traitor/60 bg-traitor/10 hover:border-traitor"
                      : "border-rim bg-panel hover:border-stone-500"
                }`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className={`text-[11px] font-black tracking-[0.14em] ${twist ? "text-traitor" : "text-candle"}`}>
                    {moment.label}
                  </span>
                  <span className="font-mono text-[10px] text-stone-400">
                    D{moment.day} · t{moment.first_tick}
                    {moment.last_tick !== moment.first_tick ? `–${moment.last_tick}` : ""}
                  </span>
                </span>
                <span className="line-clamp-3 text-[13px] leading-snug text-stone-200">{headline(moment, style)}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
