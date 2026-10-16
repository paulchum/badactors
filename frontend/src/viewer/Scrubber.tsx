// The tick scrubber: play, pause, step, and drag through the game, with the
// days marked along it and the selected moment's span lit.

import { useMemo } from "react";

import type { Game } from "../feed/load";
import type { FeedMoment } from "../feed/types";
import { when } from "../ui/common";

export function Scrubber({
  game,
  tick,
  playing,
  moment,
  onTick,
  onPlay,
}: {
  game: Game;
  tick: number;
  playing: boolean;
  moment: FeedMoment | null;
  onTick: (tick: number) => void;
  onPlay: (playing: boolean) => void;
}) {
  const last = game.lastTick;
  const frame = game.feed.positions[Math.min(tick, last)];
  const days = useMemo(() => {
    const starts: { day: number; tick: number }[] = [];
    for (const f of game.feed.positions) {
      if (starts.length === 0 || starts[starts.length - 1].day !== f.day) starts.push({ day: f.day, tick: f.tick });
    }
    return starts;
  }, [game]);
  const at = (t: number) => `${(t / Math.max(last, 1)) * 100}%`;
  const button =
    "grid h-10 w-10 shrink-0 place-items-center rounded-full border border-rim bg-ink text-lg text-stone-200 hover:border-candle/60 disabled:opacity-40";

  return (
    <div className="mt-3">
      <div className="flex items-center gap-2">
        <button type="button" className={button} onClick={() => onTick(Math.max(tick - 1, 0))} disabled={tick === 0} aria-label="Back one tick">
          ‹
        </button>
        <button
          type="button"
          className={`${button} border-candle/60 bg-candle/10 text-candle`}
          onClick={() => {
            if (!playing && tick >= last) onTick(0);
            onPlay(!playing);
          }}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <button type="button" className={button} onClick={() => onTick(Math.min(tick + 1, last))} disabled={tick === last} aria-label="Forward one tick">
          ›
        </button>
        <p className="ml-1 min-w-0 text-sm leading-tight">
          <span className="block font-semibold text-stone-100">{when(frame.day, frame.phase)}</span>
          <span className="font-mono text-xs text-stone-400">
            tick {tick} of {last}
          </span>
        </p>
      </div>
      <div className="relative mt-3 px-1">
        <div className="relative h-4 text-[10px] font-bold text-stone-500">
          {days.map((start) => (
            <span key={start.day} className="absolute -translate-x-1/2" style={{ left: at(start.tick) }}>
              {start.tick === 0 ? "" : "|"}D{start.day}
            </span>
          ))}
        </div>
        <div className="relative">
          {moment ? (
            <div
              aria-hidden
              className="pointer-events-none absolute top-1/2 h-2 -translate-y-1/2 rounded bg-candle/30"
              style={{ left: at(moment.first_tick), width: `max(6px, ${((moment.last_tick - moment.first_tick) / Math.max(last, 1)) * 100}%)` }}
            />
          ) : null}
          <input
            type="range"
            min={0}
            max={last}
            step={1}
            value={tick}
            onChange={(event) => onTick(Number(event.target.value))}
            aria-label="Tick"
            className="relative w-full accent-[#f2c46d]"
          />
        </div>
      </div>
    </div>
  );
}
