// The evidence board, one suspect at a time: what the house had on them, beside
// what the tape shows they did in secret, and the moments they are in.

import type { Game } from "../feed/load";
import { type NameStyle, type Naming, eventQuote, eventText, headline, witnessText } from "../feed/naming";
import { evidenceBoard } from "../feed/evidence";
import type { FeedEvent, FeedMoment } from "../feed/types";
import { Panel, PHASES, RollChips, SeatName } from "../ui/common";

const FATE: Record<string, string> = {
  murdered: "murdered in the night",
  poisoned: "poisoned",
  banished: "banished",
  survived: "survived to the end",
};

function Jump({ event, onJump }: { event: FeedEvent; onJump: (e: FeedEvent) => void }) {
  return (
    <button type="button" onClick={() => onJump(event)} className="mr-1.5 font-mono text-[11px] text-candle hover:underline">
      D{event.day} t{event.tick}
    </button>
  );
}

export function EvidenceBoard({
  game,
  suspect,
  style,
  naming,
  roles,
  onSuspect,
  onJump,
  onMoment,
}: {
  game: Game;
  suspect: string;
  style: NameStyle;
  naming: Naming;
  roles: boolean;
  onSuspect: (id: string) => void;
  onJump: (event: FeedEvent) => void;
  onMoment: (moment: FeedMoment) => void;
}) {
  const board = evidenceBoard(game, suspect);
  const seat = game.seat(suspect)!;
  const named = board.sightings.filter((s) => s.named === "them");
  const unnamed = board.sightings.filter((s) => s.named !== "them");
  const turned = seat.traitor_from != null ? game.event(seat.traitor_from) : null;
  const out = seat.out_at != null ? game.event(seat.out_at) : null;
  return (
    <Panel title="Evidence board">
      <div className="no-scrollbar -mx-3 mb-3 flex gap-1.5 overflow-x-auto px-3 sm:mx-0 sm:flex-wrap sm:px-0" role="tablist" aria-label="Suspects">
        {game.feed.seats.map((s) => (
          <button
            key={s.id}
            type="button"
            role="tab"
            aria-selected={s.id === suspect}
            onClick={() => onSuspect(s.id)}
            className={`shrink-0 rounded-full border px-2.5 py-1 text-xs ${
              s.id === suspect ? "border-candle bg-candle/15 text-candle" : "border-rim text-stone-300 hover:border-stone-500"
            }`}
          >
            <SeatName game={game} id={s.id} style={style} />
          </button>
        ))}
      </div>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-xl font-black">
          <SeatName game={game} id={suspect} style={style} />
        </h3>
        <span className="text-sm text-stone-400">
          {seat.build}, {seat.garment}
          {seat.detail ? `, ${seat.detail}` : ""}
        </span>
        {roles ? (
          <span className={`rounded px-2 py-0.5 text-xs font-black uppercase tracking-wider ${seat.role === "traitor" ? "bg-traitor text-white" : "bg-faithful/80 text-white"}`}>
            {seat.role === "traitor" ? (seat.recruited_by ? `traitor from day ${turned?.day}, recruited by ${naming.name(seat.recruited_by)}` : "traitor") : "faithful"}
          </span>
        ) : null}
        <span className="text-sm text-stone-400">
          {FATE[seat.fate]}
          {out ? ` on day ${out.day}` : ""}
        </span>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <div className="min-w-0">
          <h4 className="mb-1.5 text-xs font-black uppercase tracking-[0.16em] text-stone-300">What the house had on them</h4>
          <ul className="grid grid-cols-1 gap-1.5 text-sm">
            {board.votes.map((tally) => (
              <li key={tally.day} className="text-stone-300">
                Day {tally.day}: {tally.voters.length} of {tally.of} votes to banish
                {tally.banished ? <strong className="text-traitor"> — banished</strong> : null}
              </li>
            ))}
            {board.accusations.map((event) => (
              <li key={event.seq} className="rounded bg-black/30 p-2">
                <Jump event={event} onJump={onJump} />
                <SeatName game={game} id={event.actor} style={style} className="text-stone-200" />
                {eventQuote(event, style) ? <span className="block font-serif text-stone-200">“{eventQuote(event, style)}”</span> : null}
              </li>
            ))}
            <li className="text-stone-400">
              {named.length === 1 ? "One sighting names them" : `${named.length} sightings name them`}. {unnamed.length} saw only a shape or a garment.
            </li>
            {named.slice(-4).map(({ event, record }) => (
              <li key={`${event.seq}-${record.player}`} className="rounded bg-black/30 p-2 text-stone-300">
                <Jump event={event} onJump={onJump} />
                <SeatName game={game} id={record.player} style={style} className="text-stone-200" /> wrote: {witnessText(record, style)}
              </li>
            ))}
            {board.votes.length === 0 && board.accusations.length === 0 ? <li className="text-stone-500">Nobody accused them or voted against them.</li> : null}
          </ul>
        </div>
        <div className="min-w-0">
          <h4 className="mb-1.5 text-xs font-black uppercase tracking-[0.16em] text-stone-300">What they did in secret</h4>
          <ul className="grid grid-cols-1 gap-1.5 text-sm">
            {board.secrets.length === 0 ? <li className="text-stone-500">Nothing secret on the tape.</li> : null}
            {board.secrets.map((event) => (
              <li key={event.seq} className="rounded border border-traitor/30 bg-traitor/5 p-2 text-stone-200">
                <Jump event={event} onJump={onJump} />
                {eventText(event, style)}
                <span className="block text-xs text-stone-500">
                  {PHASES[event.phase]}, {game.roomName(event.room)} ·{" "}
                  {event.witnesses.filter((w) => w.sense !== "own").length} saw it
                </span>
                <RollChips rolls={event.rolls} naming={naming} />
              </li>
            ))}
          </ul>
        </div>
        <div className="min-w-0">
          <h4 className="mb-1.5 text-xs font-black uppercase tracking-[0.16em] text-stone-300">Their moments</h4>
          <ul className="grid grid-cols-1 gap-1.5 text-sm">
            {board.moments.length === 0 ? <li className="text-stone-500">None.</li> : null}
            {board.moments.map((moment) => (
              <li key={moment.id}>
                <button type="button" onClick={() => onMoment(moment)} className="w-full rounded bg-black/30 p-2 text-left hover:bg-black/50">
                  <span className="text-[11px] font-black tracking-[0.12em] text-candle">{moment.label}</span>{" "}
                  <span className="text-stone-300">{headline(moment, style)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Panel>
  );
}
