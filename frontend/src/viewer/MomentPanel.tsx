// One moment, side by side: what was said, what was written privately, and
// what the tape shows happened, with the custody of the object in question.
// Viewers here can pause, so the three run in parallel (SPEC §8); on a phone
// they stack.

import type { ReactNode } from "react";

import type { Game } from "../feed/load";
import {
  type NameStyle,
  type Naming,
  eventQuote,
  eventText,
  headline,
  privateLine,
  toldLines,
  witnessQuote,
  witnessText,
} from "../feed/naming";
import { LINE_FIELDS, LINE_NAMES, custodyChain, intentText, momentColumns, objectInQuestion, placementText } from "../feed/story";
import type { FeedEvent, FeedMoment } from "../feed/types";
import { claimWhy } from "../feed/receipt";
import { worst } from "../feed/stamps";
import { LightBadge, Panel, PHASES, RollChips, SeatName } from "../ui/common";
import { StampMark } from "./Receipt";

const CONFIDENCE: Record<string, string> = {
  clear: "text-emerald-300",
  unsure: "text-amber-300",
  glimpse: "text-rose-300",
};

function Stamp({
  game,
  moment,
  style,
  onReceipt,
}: {
  game: Game;
  moment: FeedMoment;
  style: NameStyle;
  onReceipt: (claim: string) => void;
}) {
  const claim = game.ruled(moment);
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className={`stamp stamp-in text-lg ${moment.kind === "twist_ending" ? "text-traitor" : "text-candle"}`}>{moment.label}</span>
      {claim ? (
        <>
          <StampMark stamp={claim.stamp} size="text-lg" className="stamp-in" />
          <span className="text-sm text-stone-300">{claimWhy(claim, style)}</span>
          <button type="button" onClick={() => onReceipt(claim.id)} className="text-sm text-candle underline underline-offset-2">
            See the receipt
          </button>
        </>
      ) : (
        <span className="text-xs text-stone-400">
          {moment.checked ? "Nothing in these words to check." : "Not fact-checked yet. This is just what the tape shows."}
        </span>
      )}
    </div>
  );
}

function EntryHead({
  game,
  event,
  style,
  onJump,
  paper = false,
}: {
  game: Game;
  event: FeedEvent;
  style: NameStyle;
  onJump: (e: FeedEvent) => void;
  paper?: boolean;
}) {
  return (
    <div className={`mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 font-sans text-xs not-italic ${paper ? "text-stone-600" : "text-stone-400"}`}>
      {event.actor ? <SeatName game={game} id={event.actor} style={style} className={paper ? "text-stone-900" : "text-stone-100"} /> : null}
      <button
        type="button"
        onClick={() => onJump(event)}
        className={`rounded font-mono text-[11px] underline underline-offset-2 ${
          paper ? "text-amber-900 decoration-amber-900/40 hover:decoration-amber-900" : "text-candle decoration-candle/40 hover:decoration-candle"
        }`}
        title="Show this on the map"
      >
        D{event.day} {PHASES[event.phase]} · t{event.tick}
      </button>
      {event.room ? (
        <span className="inline-flex items-center gap-1">
          {game.roomName(event.room)} <LightBadge light={event.light} />
        </span>
      ) : null}
    </div>
  );
}

function Column({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <h3 className="text-sm font-black uppercase tracking-[0.16em] text-stone-200">{title}</h3>
      <p className="mb-2 text-xs text-stone-500">{hint}</p>
      <div className="grid grid-cols-1 gap-2">{children}</div>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-lg border border-dashed border-rim p-3 text-sm text-stone-500">{children}</p>;
}

export function Witnesses({ game, event, style }: { game: Game; event: FeedEvent; style: NameStyle }) {
  const others = event.witnesses.filter((w) => w.sense !== "own");
  if (others.length === 0) return <p className="mt-2 text-xs text-stone-500">Nobody else saw it.</p>;
  const shown = others.slice(0, 6);
  return (
    <details className="mt-2 text-xs" open={others.length <= 3}>
      <summary className="cursor-pointer text-stone-400">
        What {others.length === 1 ? "one witness" : `${others.length} witnesses`} wrote down
      </summary>
      <ul className="mt-1.5 grid gap-1">
        {shown.map((record) => {
          const quote = witnessQuote(record, style);
          return (
            <li key={record.player} className="rounded-sm bg-black/30 px-2 py-1">
              <SeatName game={game} id={record.player} style={style} className="text-stone-200" />{" "}
              <span className={`font-mono text-[10px] uppercase ${CONFIDENCE[record.confidence] ?? ""}`}>
                {record.sense} · {record.confidence}
              </span>
              <span className="block text-stone-300">
                {witnessText(record, style)}
                {quote && record.channel !== "steward" ? <span className="text-stone-400"> “{quote}”</span> : null}
              </span>
            </li>
          );
        })}
        {others.length > shown.length ? <li className="text-stone-500">and {others.length - shown.length} more</li> : null}
      </ul>
    </details>
  );
}

function Custody({ game, moment, style, naming, onJump }: { game: Game; moment: FeedMoment; style: NameStyle; naming: Naming; onJump: (e: FeedEvent) => void }) {
  const thing = objectInQuestion(game, moment);
  if (thing === null) return null;
  const chain = custodyChain(game, thing, moment.last_seq);
  if (chain.length === 0) return null;
  return (
    <div className="mt-4 border-t border-rim pt-3">
      <h3 className="text-sm font-black uppercase tracking-[0.16em] text-stone-200">
        Custody: <span className="normal-case tracking-normal text-candle">{game.thingName(thing)}</span>
      </h3>
      <p className="mb-2 text-xs text-stone-500">Everyone who moved it or touched it, up to the end of the moment.</p>
      <ol className="relative grid gap-1.5 border-l border-candle/30 pl-4">
        {chain.map((link) => (
          <li key={link.seq} className="relative text-sm">
            <span aria-hidden className={`absolute left-[-21px] top-1.5 h-2.5 w-2.5 rounded-full ${link.where ? "bg-candle" : "border border-candle bg-ink"}`} />
            <button type="button" onClick={() => onJump(game.event(link.seq))} className="mr-2 font-mono text-[11px] text-stone-400 hover:text-candle">
              D{link.day} t{link.tick}
            </button>
            <span className="text-stone-200">{eventText(game.event(link.seq), style)}</span>
            {link.where ? <span className="text-stone-400"> → {placementText(link.where, game, (id) => naming.name(id))}</span> : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

export function MomentPanel({
  game,
  moment,
  style,
  naming,
  focus,
  onJump,
  onReceipt,
}: {
  game: Game;
  moment: FeedMoment;
  style: NameStyle;
  naming: Naming;
  focus: number | null;
  onJump: (event: FeedEvent) => void;
  onReceipt: (claim: string) => void;
}) {
  const columns = momentColumns(game, moment);
  const ring = (event: FeedEvent) => (event.seq === focus ? "ring-2 ring-candle/70" : "");
  const { castle, seed } = game.feed.game;
  return (
    <Panel>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <Stamp game={game} moment={moment} style={style} onReceipt={onReceipt} />
          <p className="mt-3 font-serif text-lg leading-snug text-stone-100 sm:text-xl">{headline(moment, style)}</p>
        </div>
        <p className="shrink-0 font-mono text-[11px] text-stone-500 sm:text-right">
          {castle} · seed {seed}
          <br />
          day {moment.day} · ticks {moment.first_tick}–{moment.last_tick}
        </p>
      </div>
      <div className="mt-4 grid gap-5 md:grid-cols-3 md:gap-4">
        <Column title="Said" hint="Words spoken aloud or whispered, as they were said.">
          {columns.said.length === 0 ? <Empty>Nothing was said in words.</Empty> : null}
          {columns.said.map((event) => {
            const checked = worst(game.claimsAt(event.seq));
            return (
              <article key={event.seq} className={`rounded-lg bg-black/30 p-2.5 ${ring(event)}`}>
                <EntryHead game={game} event={event} style={style} onJump={onJump} />
                {event.seq === moment.claim ? (
                  <span className="mb-1 mr-2 inline-block rounded-sm bg-candle px-1.5 text-[10px] font-black uppercase tracking-wider text-ink">the claim</span>
                ) : null}
                {checked ? (
                  <button type="button" onClick={() => onReceipt(checked.id)} className="mb-1 inline-block" title="See the receipt">
                    <StampMark stamp={checked.stamp} size="text-[11px]" className="px-1.5! py-0.5!" />
                  </button>
                ) : null}
                <p className="font-serif text-[15px] leading-snug text-stone-100">“{eventQuote(event, style)}”</p>
                <p className="mt-1 text-xs text-stone-500">{eventText(event, style)}</p>
              </article>
            );
          })}
        </Column>
        <Column title="Written privately" hint="Diaries and reasons only the audience reads.">
          {columns.privately.length === 0 ? <Empty>No private line sits on this moment.</Empty> : null}
          {columns.privately.map((event) => (
            <article key={event.seq} className={`rounded-lg p-2.5 diary ${ring(event)} ${moment.line?.seq === event.seq ? "outline-solid outline-2 outline-offset-2 outline-traitor/70" : ""}`}>
              <EntryHead game={game} event={event} style={style} onJump={onJump} paper />
              {LINE_FIELDS.map((field) => {
                const line = privateLine(event, field, style);
                return line ? (
                  <p key={field} className="text-[15px]">
                    <span className="mr-1 font-sans text-[10px] font-bold uppercase not-italic tracking-wider text-stone-600">{LINE_NAMES[field]}</span>
                    {line}
                  </p>
                ) : null;
              })}
              {event.intent ? <p className="font-sans text-xs not-italic text-stone-700">{intentText(event.intent, (id) => naming.name(id))}</p> : null}
            </article>
          ))}
        </Column>
        <Column title="What happened" hint="The tape. The first one is where a clip rewinds to.">
          {columns.happened.map((event, index) => (
            <article key={event.seq} className={`rounded-lg border border-rim bg-ink/80 p-2.5 ${ring(event)}`}>
              <EntryHead game={game} event={event} style={style} onJump={onJump} />
              {index < moment.truth.length ? (
                <span className="mb-1 inline-block rounded-sm bg-traitor px-1.5 text-[10px] font-black uppercase tracking-wider text-white">the truth</span>
              ) : null}
              <p className="text-sm text-stone-100">{eventText(event, style)}</p>
              {eventQuote(event, style) && !event.actor ? <p className="mt-1 font-serif text-sm text-stone-300">“{eventQuote(event, style)}”</p> : null}
              {toldLines(event, style).length > 0 ? (
                <ul className="mt-1 list-disc pl-4 text-xs text-stone-300">
                  {toldLines(event, style).map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              ) : null}
              <RollChips rolls={event.rolls} naming={naming} />
              <Witnesses game={game} event={event} style={style} />
            </article>
          ))}
        </Column>
      </div>
      <Custody game={game} moment={moment} style={style} naming={naming} onJump={onJump} />
    </Panel>
  );
}
