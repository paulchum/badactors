// A claim's receipt, reachable by deep link (?game=seed-56&claim=c927-1): the
// words, the stamp, the proof in plain words, the speaker's diary at the time,
// and a link to copy.

import { useState } from "react";

import { type Game, gamesUrl } from "../feed/load";
import { type NameStyle, type Naming } from "../feed/naming";
import { receiptFor, receiptLine } from "../feed/receipt";
import { GLOSSARY, INK, method, tone } from "../feed/stamps";
import type { FeedClaim, FeedEvent, Stamp } from "../feed/types";
import { href } from "../route";
import { PHASES, Panel, SeatName } from "../ui/common";

export function StampMark({ stamp, size = "text-2xl", className = "" }: { stamp: Stamp; size?: string; className?: string }) {
  return (
    <span className={`stamp inline-block border-[3px] bg-ink/60 ${size} ${INK[tone(stamp)]} ${className}`} title={GLOSSARY[stamp].means}>
      {stamp}
    </span>
  );
}

/** Where a claim's receipt lives, relative to this page. */
export function receiptHref(gameId: string, claim: FeedClaim, moment: string | null = null): string {
  return href({ page: "viewer", game: gameId, tick: claim.tick, moment, claim: claim.id });
}

/** A claim's share card, if the build made one. */
export function cardHref(gameId: string, claim: FeedClaim): string {
  return gamesUrl(`cards/${gameId}/${claim.id}.svg`);
}

export function ClaimReceipt({
  game,
  gameId,
  claim,
  style,
  naming,
  onClose,
  onJump,
}: {
  game: Game;
  gameId: string;
  claim: FeedClaim;
  style: NameStyle;
  naming: Naming;
  onClose?: () => void;
  onJump?: (event: FeedEvent) => void;
}) {
  const receipt = receiptFor(game, claim, style, naming);
  const [copied, setCopied] = useState<string | null>(null);
  const link = receiptHref(gameId, claim, receipt.moment?.id ?? null);
  const absolute = typeof window === "undefined" ? link : new URL(link, window.location.href).toString();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(absolute);
      setCopied("Copied.");
    } catch {
      setCopied("Couldn't copy from here. The link's right there.");
    }
  };
  const meaning = GLOSSARY[claim.stamp];
  return (
    <Panel className="border-candle/50" title={<span id="receipt">The receipt</span>}>
      <article aria-labelledby="receipt" className="grid gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-mono text-[11px] text-stone-400">
              Day {claim.day}, {PHASES[claim.phase]} · tick {claim.tick}
            </p>
            <SeatName game={game} id={claim.speaker} style={style} className="mt-1 text-lg text-stone-100" />
            <p className="mt-2 font-serif text-2xl leading-snug text-stone-50">“{receipt.quote}”</p>
            <p className="mt-2 text-sm text-stone-400">In plain words: {receipt.what}.</p>
          </div>
          {onClose ? (
            <button type="button" onClick={onClose} className="shrink-0 rounded-full border border-rim px-3 py-1 text-sm text-stone-300 hover:border-candle">
              Close
            </button>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <StampMark stamp={claim.stamp} size="text-3xl" className="stamp-in" />
          <p className="min-w-0 flex-1 text-lg font-semibold text-stone-100">{receipt.why}</p>
        </div>
        <p className="text-xs text-stone-500">{meaning.means}</p>

        {receipt.proof.length > 0 ? (
          <div className="grid gap-3 md:grid-cols-3">
            {receipt.proof.map((group) => (
              <section key={group.title} className="min-w-0">
                <h3 className="mb-1.5 text-xs font-black uppercase tracking-[0.16em] text-stone-300">{group.title}</h3>
                <ol className="grid gap-1.5 text-sm">
                  {group.lines.map((line) => (
                    <li key={line.seq} className="rounded-sm bg-black/30 p-2 text-stone-200">
                      {onJump ? (
                        <button
                          type="button"
                          onClick={() => onJump(game.event(line.seq))}
                          className="mr-1.5 font-mono text-[11px] text-candle hover:underline"
                        >
                          D{line.day} t{line.tick}
                        </button>
                      ) : (
                        <span className="mr-1.5 font-mono text-[11px] text-stone-400">
                          D{line.day} t{line.tick}
                        </span>
                      )}
                      {line.text}
                    </li>
                  ))}
                </ol>
              </section>
            ))}
          </div>
        ) : (
          <p className="text-sm text-stone-400">The words are the whole story here: there's nothing else on the tape to hold up.</p>
        )}

        <section>
          <h3 className="mb-1.5 text-xs font-black uppercase tracking-[0.16em] text-stone-300">
            {receipt.speaker}'s diary{receipt.diary && !receipt.diary.same ? `, as of tick ${receipt.diary.tick}` : ""}
          </h3>
          {receipt.diary ? (
            <p className="diary rounded-lg p-3 text-lg shadow-lg">“{receipt.diary.text}”</p>
          ) : (
            <p className="text-sm text-stone-500">Nothing written down. Not a word.</p>
          )}
        </section>

        <div className="grid gap-2 border-t border-rim pt-3">
          <label className="flex flex-wrap items-center gap-2 text-sm text-stone-400">
            <span>Link to this receipt</span>
            <input readOnly value={absolute} className="min-w-0 flex-1 rounded-sm border border-rim bg-black/40 px-2 py-1 font-mono text-xs text-stone-200" onFocus={(e) => e.currentTarget.select()} />
            <button type="button" onClick={copy} className="rounded-full bg-candle px-3 py-1 font-semibold text-ink">
              Copy
            </button>
            <a className="rounded-full border border-rim px-3 py-1 text-stone-200 hover:border-candle" href={cardHref(gameId, claim)}>
              Share card
            </a>
          </label>
          {copied ? <p className="text-xs text-stone-400">{copied}</p> : null}
          <p className="font-mono text-[11px] text-stone-500">{receiptLine(game, claim)}</p>
          <p className="font-mono text-[11px] text-stone-600">Fact-checker's class: {method(claim)}</p>
        </div>
      </article>
    </Panel>
  );
}
