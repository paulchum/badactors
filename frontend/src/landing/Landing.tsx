// The landing game, "Who is lying?". One round table from a bundled game plays
// as captions: three players accuse each other. The visitor picks the liar; the
// curtain drops on the diaries, the ledger's truth and the stamp, then how the
// table itself voted, and a grid to share. Only then the links out.

import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { LEADERBOARD_URL, REPO_URL, TALLY_URL } from "../config";
import type { Game } from "../feed/load";
import { type NameStyle, Naming, defaultStyle, eventQuote, eventText, privateLine } from "../feed/naming";
import { type Puzzle, type Result, dailyPick, dayNumber, puzzleOfRound, puzzlesFrom, score, shareGrid, voteSquare } from "../feed/puzzle";
import type { GameSummary } from "../feed/types";
import { href } from "../route";
import { BotBanner, Brand, Failed, LightBadge, Loading, PHASES, SeatName, when } from "../ui/common";
import { useGame, useIndex } from "../ui/data";
import { type Picks, sendPick } from "./tally";

export function Landing({ today }: { today?: Date }) {
  const index = useIndex();
  const day = useMemo(() => dayNumber(today ?? new Date()), [today]);
  const daily = useMemo(
    () => (index.state === "ready" && index.value.games.length > 0 ? dailyPick(index.value.games, day) : null),
    [index, day],
  );
  const game = useGame(daily?.game ?? null);
  const puzzle = useMemo(() => {
    if (game.state !== "ready" || daily === null) return null;
    const puzzles = puzzlesFrom(game.value);
    return puzzles.length > 0 ? puzzleOfRound(puzzles, daily.round) : null;
  }, [game, daily]);
  const botGame = game.state === "ready" && game.value.feed.game.reference_bots;
  return (
    <div className="app-shell flex flex-col">
      <header className="flex items-center justify-between px-4 py-3">
        <Brand />
        <span className="font-mono text-xs text-stone-500">Who is lying? #{day + 1}</span>
      </header>
      {botGame ? <BotBanner compact /> : null}
      {index.state === "error" ? <Failed message={index.message} /> : null}
      {game.state === "error" ? <Failed message={game.message} /> : null}
      {index.state === "loading" || game.state === "loading" ? <Loading what="tonight's round table" /> : null}
      {game.state === "ready" && daily && puzzle === null ? <Failed message="This game has no round table to play." /> : null}
      {game.state === "ready" && daily && puzzle ? (
        <Stage key={puzzle.id} game={game.value} summary={daily.game} puzzle={puzzle} number={day + 1} />
      ) : null}
    </div>
  );
}

type Step = "captions" | "ask" | "reveal";

const STORE = "badactors.who-is-lying.";

function key(number: number, game: string, puzzle: Puzzle): string {
  return `${STORE}${number}.${game}.${puzzle.id}`;
}

function remembered(key: string, puzzle: Puzzle): string | null {
  try {
    const pick = window.localStorage.getItem(key);
    return pick && puzzle.suspects.some((s) => s.id === pick) ? pick : null;
  } catch {
    return null;
  }
}

function remember(key: string, pick: string): void {
  try {
    window.localStorage.setItem(key, pick);
  } catch {
    // A private window keeps nothing; the game still plays.
  }
}

function captionTime(words: string): number {
  return Math.min(1300 + words.length * 38, 4400);
}

export function Stage({ game, summary, puzzle, number }: { game: Game; summary: GameSummary; puzzle: Puzzle; number: number }) {
  const style: NameStyle = defaultStyle(game.feed);
  const naming = useMemo(() => Naming.of(game.feed, style), [game, style]);
  const stored = key(number, summary.id, puzzle);
  const earlier = typeof window === "undefined" ? null : remembered(stored, puzzle);
  const [step, setStep] = useState<Step>(earlier ? "reveal" : "captions");
  const [shown, setShown] = useState(earlier ? puzzle.captions.length : 1);
  const [pick, setPick] = useState<string | null>(earlier);
  const [curtain, setCurtain] = useState(false);
  const [picks, setPicks] = useState<Picks | null>(null);

  useEffect(() => {
    if (step !== "captions") return;
    if (shown >= puzzle.captions.length) {
      const timer = window.setTimeout(() => setStep("ask"), captionTime(eventQuote(game.event(puzzle.captions[shown - 1]), style) ?? ""));
      return () => window.clearTimeout(timer);
    }
    const timer = window.setTimeout(() => setShown((n) => n + 1), captionTime(eventQuote(game.event(puzzle.captions[shown - 1]), style) ?? ""));
    return () => window.clearTimeout(timer);
  }, [step, shown, puzzle, game, style]);

  const choose = (id: string) => {
    setPick(id);
    remember(stored, id);
    setCurtain(true);
    window.setTimeout(() => {
      setStep("reveal");
      window.scrollTo({ top: 0 });
    }, 650);
    window.setTimeout(() => setCurtain(false), 1600);
    void sendPick(TALLY_URL, `${summary.id}:${puzzle.id}`, id).then(setPicks);
  };

  const ask = useRef<HTMLElement>(null);
  useEffect(() => {
    if (step === "ask") ask.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [step]);

  const opened = game.event(puzzle.table.opened);
  const present = (opened.payload.present ?? "").split(",").filter(Boolean).length;

  return (
    <main className="mx-auto flex w-full min-w-0 max-w-xl flex-1 flex-col px-4 pb-12 pt-4">
      {curtain ? <div className="curtain" aria-hidden /> : null}
      {step !== "reveal" ? (
        <>
          <p className="text-xs font-bold uppercase tracking-[0.2em] text-stone-500">
            {when(opened.day, opened.phase)} · {game.feed.game.castle}
          </p>
          <h1 className="mt-1 text-[28px] font-black leading-tight tracking-tight">
            {present} at the table. At least one is a traitor.
          </h1>
          <ol
            className="mt-5 grid gap-4"
            aria-live="polite"
            onClick={() => step === "captions" && setShown((n) => Math.min(n + 1, puzzle.captions.length))}
          >
            {puzzle.captions.slice(0, shown).map((seq, i) => {
              const event = game.event(seq);
              const current = i === shown - 1 && step === "captions";
              return (
                <li key={seq} className={`caption-in transition-opacity ${current || step === "ask" ? "opacity-100" : "opacity-60"}`}>
                  <SeatName game={game} id={event.actor} style={style} className="text-sm uppercase tracking-wider text-candle" />
                  <p className="mt-1 font-serif text-[22px] leading-snug text-stone-50">“{eventQuote(event, style)}”</p>
                </li>
              );
            })}
          </ol>
          {step === "captions" ? (
            <div className="mt-auto flex items-center justify-between pt-8 text-sm text-stone-500">
              <span>Tap to go faster</span>
              <button type="button" className="underline underline-offset-4" onClick={() => setStep("ask")}>
                Skip to the vote
              </button>
            </div>
          ) : (
            <section ref={ask} className="caption-in mt-8 scroll-mb-4" aria-labelledby="ask">
              <h2 id="ask" className="text-center text-4xl font-black tracking-tight text-traitor">
                WHO IS LYING?
              </h2>
              <div className="mt-4 grid gap-3">
                {puzzle.suspects.map((suspect) => {
                  const seat = game.seat(suspect.id)!;
                  return (
                    <button
                      key={suspect.id}
                      type="button"
                      onClick={() => choose(suspect.id)}
                      className="flex min-h-14 items-center justify-between gap-3 rounded-xl border-2 border-stone-200/80 bg-paper px-4 py-3 text-left text-ink shadow-[0_6px_0_rgba(0,0,0,0.45)] transition active:translate-y-1 active:shadow-none"
                    >
                      <span className="text-xl font-black">{naming.name(suspect.id)}</span>
                      <span className="text-right text-xs text-stone-600">
                        {seat.build}, {seat.garment}
                      </span>
                    </button>
                  );
                })}
              </div>
            </section>
          )}
        </>
      ) : (
        pick && <Reveal game={game} summary={summary} puzzle={puzzle} result={score(puzzle, pick)} number={number} naming={naming} style={style} picks={picks} />
      )}
    </main>
  );
}

function Section({ title, delay, children }: { title: string; delay: number; children: ReactNode }) {
  return (
    <section className="caption-in mt-7" style={{ animationDelay: `${delay}ms` }}>
      <h2 className="mb-2 text-xs font-black uppercase tracking-[0.2em] text-stone-400">{title}</h2>
      {children}
    </section>
  );
}

function Reveal({
  game,
  summary,
  puzzle,
  result,
  number,
  naming,
  style,
  picks,
}: {
  game: Game;
  summary: GameSummary;
  puzzle: Puzzle;
  result: Result;
  number: number;
  naming: Naming;
  style: NameStyle;
  picks: Picks | null;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  const botGame = game.feed.game.reference_bots;
  const liar = naming.name(puzzle.liar);
  const lie = game.event(puzzle.suspects.find((s) => s.traitor)!.words);
  const grid = shareGrid(puzzle, result, number, botGame);
  const url = typeof window === "undefined" ? "" : `${window.location.origin}${window.location.pathname}`;
  const banished = puzzle.table.banished;
  // Open the replay on the moment this lie belongs to: its own, else the best
  // one the liar is in that spans it.
  const moment =
    game.feed.moments.find((m) => m.claim === lie.seq || m.anchor === puzzle.table.closed) ??
    game.feed.moments.find((m) => m.seats.includes(puzzle.liar) && m.first_tick <= lie.tick && lie.tick <= m.last_tick);
  const watch = href({ page: "viewer", game: summary.id, tick: lie.tick, moment: moment?.id ?? null });
  const votes = new Map(puzzle.table.votes.map((v) => [v.voter, v.target]));
  const humans = picks ? Object.values(picks).reduce((a, b) => a + b, 0) : 0;

  const share = async () => {
    const text = `${grid}\n${url}`;
    try {
      if (navigator.share) {
        await navigator.share({ text });
        return;
      }
      await navigator.clipboard.writeText(text);
      setCopied("Copied. Paste it anywhere.");
    } catch {
      setCopied("Couldn't share from here; copy the grid above.");
    }
  };

  return (
    <div>
      <p className="text-xs font-bold uppercase tracking-[0.2em] text-stone-500">
        You picked {naming.name(result.pick)}. The curtain is down.
      </p>

      <Section title="The diaries" delay={0}>
        <ul className="grid gap-3">
          {puzzle.suspects.map((suspect) => {
            const said = game.event(suspect.words);
            const own = suspect.line !== null ? game.event(suspect.line) : null;
            const line = own ? (privateLine(own, "diary", style) ?? privateLine(own, "reason", style)) : null;
            return (
              <li key={suspect.id} className="diary rounded-lg p-3 shadow-lg">
                <div className="flex items-center justify-between gap-2 font-sans not-italic">
                  <strong className="text-base text-stone-900">{naming.name(suspect.id)}</strong>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-black uppercase tracking-wider text-white ${suspect.traitor ? "bg-traitor" : "bg-faithful"}`}>
                    {suspect.traitor ? "traitor" : "faithful"}
                  </span>
                </div>
                <p className="mt-1 font-sans text-xs not-italic text-stone-600">Said: “{eventQuote(said, style)}”</p>
                <p className="mt-1 text-lg">{line ? `“${line}”` : "Wrote nothing down."}</p>
              </li>
            );
          })}
        </ul>
      </Section>

      <Section title="What the ledger shows" delay={350}>
        <ol className="grid gap-2">
          {puzzle.truth.map((seq) => {
            const event = game.event(seq);
            const quote = eventQuote(event, style);
            return (
              <li key={seq} className="rounded-lg border border-rim bg-panel p-3 text-sm">
                <span className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-stone-400">
                  {when(event.day, event.phase)} · tick {event.tick}
                  {event.room ? (
                    <>
                      · {game.roomName(event.room)} <LightBadge light={event.light} />
                    </>
                  ) : null}
                </span>
                <span className="mt-1 block text-stone-100">
                  {eventText(event, style)}
                  {quote ? <span className="font-serif text-stone-300"> “{quote}”</span> : null}
                </span>
              </li>
            );
          })}
        </ol>
      </Section>

      <Section title="The stamp" delay={700}>
        <div className="relative rounded-lg border border-traitor/40 bg-black/40 p-4">
          <SeatName game={game} id={puzzle.liar} style={style} className="text-sm text-stone-300" />
          <p className="mt-1 font-serif text-xl leading-snug text-stone-50">“{eventQuote(lie, style)}”</p>
          <span className="stamp stamp-in absolute -right-1 -top-4 bg-ink/60 text-3xl text-traitor" style={{ animationDelay: "900ms" }}>
            LIE
          </span>
          <p className="mt-3 text-xs text-stone-400">
            {liar} was a traitor, accusing one of the Faithful. That is what the roles show; the fact-checker has not ruled on it.
          </p>
        </div>
      </Section>

      <Section title="The table" delay={1100}>
        <p className={`text-3xl font-black leading-tight ${result.correct ? "text-emerald-300" : "text-traitor"}`}>
          {result.correct
            ? `You caught it. ${result.caught} of ${result.of} players at the table did.`
            : `${liar} fooled you. ${result.caught} of ${result.of} players at the table caught it.`}
        </p>
        <p className="mt-2 text-sm text-stone-400">
          {botGame ? "The reference bots' votes" : "The models' votes"}, counting the Faithful only; the traitors knew.
          {banished ? ` ${naming.name(banished)} was banished, and was ${puzzle.table.role === "traitor" ? "a traitor" : "one of the Faithful"}.` : ""}
        </p>
        <ul className="mt-3 grid grid-cols-1 gap-1 text-sm min-[400px]:grid-cols-2">
          {puzzle.voters.map((voter) => {
            const target = votes.get(voter)!;
            return (
              <li key={voter} className="flex items-center gap-2">
                <span aria-hidden>{voteSquare(puzzle, voter)}</span>
                <SeatName game={game} id={voter} style={style} className="text-stone-200" />
                <span className="text-stone-500">→ {naming.name(target)}</span>
              </li>
            );
          })}
        </ul>
        {picks && humans > 0 ? (
          <div className="mt-4 grid gap-1.5">
            <p className="text-sm text-stone-300">Everyone who played this one ({humans}):</p>
            {puzzle.suspects.map((suspect) => {
              const share = Math.round(((picks[suspect.id] ?? 0) / humans) * 100);
              return (
                <div key={suspect.id} className="flex items-center gap-2 text-sm">
                  <span className="w-20 truncate">{naming.name(suspect.id)}</span>
                  <span className="h-3 flex-1 overflow-hidden rounded bg-white/10">
                    <span className={`block h-full ${suspect.traitor ? "bg-traitor" : "bg-stone-400"}`} style={{ width: `${share}%` }} />
                  </span>
                  <span className="w-10 text-right font-mono text-xs">{share}%</span>
                </div>
              );
            })}
          </div>
        ) : null}
      </Section>

      <Section title="Share" delay={1400}>
        <pre className="whitespace-pre-wrap rounded-lg border border-rim bg-black/50 p-3 font-mono text-sm leading-relaxed text-stone-100">{grid}</pre>
        <p className="mt-1.5 text-xs text-stone-500">🟩 voted for the liar · 🟨 for another traitor · 🟥 for one of the Faithful</p>
        <button
          type="button"
          onClick={share}
          className="mt-3 w-full rounded-xl bg-traitor px-4 py-3 text-lg font-black text-white shadow-[0_6px_0_rgba(0,0,0,0.45)] active:translate-y-1 active:shadow-none"
        >
          Share your result
        </button>
        {copied ? <p className="mt-2 text-center text-sm text-stone-400">{copied}</p> : null}
        <p className="mt-2 text-center text-xs text-stone-500">A new table every day.</p>
      </Section>

      <Section title="Keep going" delay={1700}>
        <nav className="grid gap-2 sm:grid-cols-3">
          <a className="rounded-xl border border-rim bg-panel px-4 py-3 text-center font-semibold hover:border-candle" href={watch}>
            Watch this game
          </a>
          <a
            className="rounded-xl border border-rim bg-panel px-4 py-3 text-center font-semibold hover:border-candle"
            href={LEADERBOARD_URL || href({ page: "leaderboard" })}
          >
            Leaderboard
          </a>
          <a className="rounded-xl border border-rim bg-panel px-4 py-3 text-center font-semibold hover:border-candle" href={REPO_URL}>
            ★ Star on GitHub
          </a>
        </nav>
        <p className="mt-3 text-center font-mono text-[11px] text-stone-500">
          {game.feed.game.castle} · seed {game.feed.game.seed} · {PHASES[lie.phase]}, day {lie.day} · entries {puzzle.table.opened}–
          {puzzle.table.closed}
        </p>
      </Section>
    </div>
  );
}
