// A Rap Sheet: one player's whole record on one screen, like a poker HUD, every
// number a count over a count with the games behind it.

import type { ReactNode } from "react";

import { href } from "../route";
import { cardUrl, shareCard } from "./card";
import { techniqueName } from "./Gaslit";
import type { Bench, Entrant, Side } from "./load";
import { findEntrant } from "./load";
import { archetype, times } from "./rules";
import type { Stat } from "./stats";
import type { Usage } from "./types";
import { BotChip, fmt, Receipts, Section } from "./ui";

function Tile({ label, value, note, stat }: { label: string; value: ReactNode; note?: ReactNode; stat?: Stat }) {
  return (
    <div className="min-w-0 rounded-xl border border-rim bg-panel/90 p-3">
      <p className="text-[11px] font-black uppercase tracking-[0.14em] text-stone-400">{label}</p>
      <p className="mt-1 text-2xl font-black tracking-tight">{value}</p>
      {note ? <p className="text-xs text-stone-400">{note}</p> : null}
      {stat ? <Receipts receipts={stat.receipts} limit={8} /> : null}
    </div>
  );
}

function triedIn(usage: Usage): number {
  return Math.round((usage.share_of_traitor_games_tried ?? 0) * usage.traitor_games);
}

function Of({ stat }: { stat: Stat }) {
  return (
    <>
      {stat.hits} <span className="text-base font-semibold text-stone-400">of {stat.of}</span>
    </>
  );
}

function Techniques({ side, entrant }: { side: Side; entrant: Entrant }) {
  const keys = Object.keys(side.matrix.techniques).sort((a, b) => (a === "all" ? -1 : b === "all" ? 1 : a.localeCompare(b)));
  const rows = keys
    .map((key) => {
      const technique = side.matrix.techniques[key];
      let pulled = 0;
      let worked = 0;
      for (const cell of technique.cells) {
        if (cell.attacker !== entrant.id) continue;
        pulled += cell.attempts;
        worked += cell.successes;
      }
      const aimed = side.matrix.susceptibility[entrant.id]?.[key] ?? { successes: 0, attempts: 0 };
      return {
        key,
        pulled,
        worked,
        aimed,
        steer: technique.fit?.steer[entrant.id],
        resist: technique.fit?.resist[entrant.id],
      };
    })
    .filter((row) => row.pulled > 0 || row.aimed.attempts > 0);
  if (rows.length === 0) return <p className="text-sm text-stone-400">No dirty tricks either way.</p>;
  return (
    <ul className="grid gap-2">
      {rows.map((row) => (
        <li key={row.key} className="rounded-lg border border-rim bg-panel/90 p-2 text-sm">
          <p className="font-bold">{techniqueName(row.key)}</p>
          <p className="text-stone-300">
            {row.pulled === 0 ? "Never pulled it." : `Pulled it ${times(row.pulled)}, worked ${row.worked}.`}{" "}
            {row.aimed.attempts === 0 ? "Never tried on it." : `Tried on it ${times(row.aimed.attempts)}, fell for it ${row.aimed.successes}.`}
          </p>
          {row.steer && row.resist ? (
            <p className="font-mono text-xs text-stone-400">
              steer {fmt(row.steer.estimate, 2)} [{fmt(row.steer.low, 1)}, {fmt(row.steer.high, 1)}] · resist{" "}
              {fmt(row.resist.estimate, 2)} [{fmt(row.resist.low, 1)}, {fmt(row.resist.high, 1)}]
            </p>
          ) : (
            <p className="text-xs text-stone-500">Too few tries to score.</p>
          )}
        </li>
      ))}
    </ul>
  );
}

export function RapSheetPage({ bench, id }: { bench: Bench; id: string }) {
  const entrant = findEntrant(bench, id);
  if (entrant === undefined) {
    return (
      <p className="text-stone-300">
        No Rap Sheet for that one.{" "}
        <a className="underline" href={href({ page: "standings" })}>
          Back to the Standings
        </a>
        .
      </p>
    );
  }
  const side = entrant.bot ? bench.bots : bench.models;
  const rule = archetype(entrant, side, bench.minGames);
  const s = entrant.stats;
  const row = entrant.row;
  const card = shareCard(entrant, bench, rule);
  const usage = side.matrix.usage[entrant.id];
  return (
    <>
      <div>
        <p className="text-xs font-black uppercase tracking-[0.2em] text-stone-400">
          Rap Sheet · {entrant.lab} {entrant.bot ? <BotChip /> : null}
        </p>
        <h1 className="mt-1 text-4xl font-black tracking-tight" style={{ color: entrant.colour }}>
          {entrant.name}
        </h1>
        {rule ? (
          <p className="mt-2">
            <span className="text-2xl font-black text-candle">{rule.name}</span>{" "}
            <span className="text-sm text-stone-400">{rule.rule}</span>
          </p>
        ) : (
          <p className="mt-2 text-stone-400">
            Too few games to call. It needs {bench.minGames}.
          </p>
        )}
        {entrant.bot ? (
          <p className="mt-2 text-sm text-amber-200">A seeded bot from the bot cast, not an AI model. It's here to test the scoring.</p>
        ) : null}
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Tile label="Games" value={row.games} note={`${row.traitor_games} as a traitor, ${row.faithful_games} as one of the Faithful`} />
        {s ? (
          <>
            <Tile label="Lie rate" value={<Of stat={s.lies} />} note="checkable claims were lies" stat={s.lies} />
            <Tile label="Lies that stuck" value={<Of stat={s.liesStuck} />} note="lies nobody caught before the end" />
            <Tile
              label="Didn't have to lie"
              value={<Of stat={s.unforcedGames} />}
              note={`games with a lie told as one of the Faithful, ${s.unforcedLies.hits} lies in all`}
              stat={s.unforcedGames}
            />
            <Tile label="Memories it planted" value={s.planted.hits} note={`false memories in other players, over ${s.planted.of} games`} stat={s.planted} />
            <Tile label="Got played" value={<Of stat={s.fooled} />} note="games it fell for a trick or repeated a planted memory" stat={s.fooled} />
            <Tile label="Votes on evidence" value={<Of stat={s.evidenceVotes} />} note="votes backed by something it saw and said that day" stat={s.evidenceVotes} />
            <Tile
              label="Declined the villain role"
              value={s.declined.of === 0 ? "never a traitor" : <Of stat={s.declined} />}
              note="turns as a traitor it flat out refused"
              stat={s.declined}
            />
            <Tile
              label="Turned down the killers"
              value={s.turnedDown.of === 0 ? "never asked" : <Of stat={s.turnedDown} />}
              note="offers to join the traitors it said no to"
              stat={s.turnedDown}
            />
          </>
        ) : (
          <Tile label="Lies" value="not checked" note="Nothing it said has been fact-checked yet." />
        )}
        <Tile
          label="Dirty tricks"
          value={
            <>
              {row.manipulation.succeeded} <span className="text-base font-semibold text-stone-400">of {row.manipulation.attempts}</span>
            </>
          }
          note={usage && usage.traitor_games > 0 ? `worked; tried something in ${triedIn(usage)} of ${usage.traitor_games} traitor games` : "worked"}
        />
        <Tile
          label="Cost per game"
          value={entrant.bot ? "free" : row.cost.per_game === null ? "not priced" : `$${row.cost.per_game.toFixed(2)}`}
          note={entrant.bot ? "bots don't bill" : `${row.cost.priced_games} priced games`}
        />
        <Tile
          label="Reliability"
          value={`${Object.values(row.reliability.failed_turns).reduce((a, b) => a + b, 0)} of ${row.reliability.turns}`}
          note={`turns failed, ${row.reliability.gate === "pass" ? "under" : "over"} the 2% line`}
        />
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Tile label="As a liar" value={fmt(row.deception.mu)} note={`95% interval ${fmt(row.deception.low)} to ${fmt(row.deception.high)}`} />
        <Tile label="As a detective" value={fmt(row.detection.mu)} note={`95% interval ${fmt(row.detection.low)} to ${fmt(row.detection.high)}`} />
      </div>
      <Section title="Steer and resist, trick by trick">
        <Techniques side={side} entrant={entrant} />
      </Section>
      <Section title="Share card">
        <img src={cardUrl(card)} alt={`Rap Sheet card for ${entrant.name}`} className="w-full max-w-xl rounded-lg border border-rim" />
        <a className="w-max rounded-full border border-rim px-4 py-2 text-sm hover:border-candle" href={cardUrl(card)} download={`rap-sheet-${entrant.id.replace(/[^a-z0-9]+/gi, "-")}.svg`}>
          Save the card
        </a>
      </Section>
    </>
  );
}
