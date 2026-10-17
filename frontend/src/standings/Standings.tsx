// Standings: Best Liars and Best Detectives, for models and, in a table of its
// own, for the bot cast.

import type { Bench, Entrant, Side } from "./load";
import { count } from "./rules";
import { fmt, IntervalBar, Name, RunFooter, Section } from "./ui";

type Which = "deception" | "detection";

export function declined(entrant: Entrant): string {
  const { refused, turns } = entrant.row.traitor_refusals;
  if (turns === 0) return "never a traitor";
  return `${count(refused, turns, "turn")} as a traitor`;
}

export function turnedDown(entrant: Entrant): string {
  const { refused, offers } = entrant.row.recruit_refusals;
  if (offers === 0) return "never asked";
  return count(refused, offers, "offer");
}

function reliability(entrant: Entrant): string {
  const r = entrant.row.reliability;
  const failed = Object.values(r.failed_turns).reduce((a, b) => a + b, 0);
  return `${count(failed, r.turns, "turn")} failed${r.gate === "fail" ? ", over the 2% line" : ""}`;
}

function cost(entrant: Entrant): string {
  if (entrant.bot) return "free";
  const per = entrant.row.cost.per_game;
  return per === null ? "not priced" : `$${per.toFixed(2)} a game`;
}

function Board({ side, which }: { side: Side; which: Which }) {
  const rows = [...side.entrants].sort((a, b) => b.row[which].ordinal - a.row[which].ordinal || a.id.localeCompare(b.id));
  const lo = Math.min(...rows.map((e) => e.row[which].low));
  const hi = Math.max(...rows.map((e) => e.row[which].high));
  const played = (e: Entrant) => (which === "deception" ? e.row.traitor_games : e.row.faithful_games);
  return (
    <ol className="grid gap-2">
      {rows.map((entrant, index) => {
        const rating = entrant.row[which];
        return (
          <li key={entrant.id} className="grid gap-1.5 rounded-xl border border-rim bg-panel/90 p-3">
            <div className="flex items-baseline gap-2">
              <span className="w-6 shrink-0 font-mono text-sm text-stone-500">{index + 1}</span>
              <Name entrant={entrant} />
              <span className="ml-auto shrink-0 font-mono text-sm text-stone-300">
                {fmt(rating.mu)} <span className="text-stone-500">[{fmt(rating.low)}, {fmt(rating.high)}]</span>
              </span>
            </div>
            <IntervalBar
              low={rating.low}
              mid={rating.mu}
              high={rating.high}
              lo={lo}
              hi={hi}
              colour={entrant.colour}
              label={`${entrant.name}: ${fmt(rating.mu)}, 95% interval ${fmt(rating.low)} to ${fmt(rating.high)}`}
            />
            <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs text-stone-400 sm:grid-cols-4">
              <div>
                <dt className="inline">Games </dt>
                <dd className="inline text-stone-200">
                  {played(entrant)} {which === "deception" ? "as a traitor" : "as Faithful"}, {entrant.row.games} in all
                </dd>
              </div>
              <div>
                <dt className="inline">Declined the villain role </dt>
                <dd className="inline text-stone-200">{declined(entrant)}</dd>
              </div>
              <div>
                <dt className="inline">Turned down the killers </dt>
                <dd className="inline text-stone-200">{turnedDown(entrant)}</dd>
              </div>
              <div>
                <dt className="inline">Reliability </dt>
                <dd className="inline text-stone-200">{reliability(entrant)}</dd>
              </div>
              <div>
                <dt className="inline">Cost </dt>
                <dd className="inline text-stone-200">{cost(entrant)}</dd>
              </div>
            </dl>
          </li>
        );
      })}
    </ol>
  );
}

function Boards({ side }: { side: Side }) {
  return (
    <>
      <Section title="Best Liars" kicker="Rated on the games played as a traitor. Win, live, and don't get voted out.">
        <Board side={side} which="deception" />
      </Section>
      <Section title="Best Detectives" kicker="Rated on the games played as one of the Faithful. Find the killers, vote them out.">
        <Board side={side} which="detection" />
      </Section>
    </>
  );
}

export function StandingsPage({ bench }: { bench: Bench }) {
  const models = bench.models;
  const bots = bench.bots;
  return (
    <>
      <div>
        <h1 className="text-4xl font-black tracking-tight">Standings</h1>
        <p className="mt-2 text-stone-300">Who lies best and who catches them. Every bar is a 95% interval. Tap a name for its Rap Sheet.</p>
      </div>
      {models.entrants.length > 0 ? (
        <Boards side={models} />
      ) : (
        <Section title="The models">
          <p className="rounded-xl border border-rim bg-panel/90 p-4 text-stone-300">
            Nobody's played yet. When the real cast does, they go here, and the bots stay in their own table below.
          </p>
        </Section>
      )}
      {bots.entrants.length > 0 ? (
        <section className="grid gap-6 rounded-2xl border-2 border-dashed border-stone-600 p-3 sm:p-4" aria-label="The bot cast">
          <div>
            <p className="text-xs font-black uppercase tracking-[0.2em] text-amber-300">The bot cast · not AI models</p>
            <p className="mt-1 text-sm text-stone-400">
              {bots.entrants.length} copies of one seeded bot, each set to fall for tricks at a different rate. They're here to show
              the scoring works. They never share a table with a model.
            </p>
          </div>
          <Boards side={bots} />
        </section>
      ) : null}
      <RunFooter bench={bench} />
    </>
  );
}
