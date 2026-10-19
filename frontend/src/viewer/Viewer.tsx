// The replay site: the map and the scrubber, the highlights, one moment's said,
// thought and done side by side, and the evidence board. Deep links carry the
// game, the tick and the moment, ?game=seed-56&tick=41&moment=twist_ending-1110,
// and a claim's receipt opens with &claim=c927-1.

import { useEffect, useMemo, useState } from "react";

import { planFor } from "../feed/layout";
import type { Game } from "../feed/load";
import { type NameStyle, Naming, defaultStyle, eventText } from "../feed/naming";
import { tokenTimeline, tokensAt } from "../feed/positions";
import { titleLine } from "../feed/receipt";
import type { FeedEvent, FeedMoment, GameIndex, GameSummary } from "../feed/types";
import type { Route } from "../route";
import { BotBanner, Brand, Failed, Loading, Panel, SeatName, when } from "../ui/common";
import { useGame, useIndex } from "../ui/data";
import { CastleMap, MapKey, garmentColour, initials } from "./CastleMap";
import { EvidenceBoard } from "./EvidenceBoard";
import { HighlightsRail, railOrder } from "./HighlightsRail";
import { MomentPanel } from "./MomentPanel";
import { ClaimReceipt } from "./Receipt";
import { Scrubber } from "./Scrubber";

type ViewerRoute = Extract<Route, { page: "viewer" }>;

/** Entries too routine to list under a tick. */
const ROUTINE = new Set(["plan_formed", "plan_cut", "replan_capped", "wait", "placed", "use", "contribution", "summoned", "phase_opened"]);

export function Viewer({ route, go }: { route: ViewerRoute; go: (next: Route, how?: "push" | "replace") => void }) {
  const index = useIndex();
  const summary = index.state === "ready" ? (index.value.games.find((g) => g.id === route.game) ?? index.value.games[0] ?? null) : null;
  const game = useGame(summary);
  return (
    <div className="app-shell">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-6">
        <Brand />
        {index.state === "ready" && summary ? <GamePicker index={index.value} current={summary} go={go} /> : null}
      </header>
      {index.state === "error" ? <Failed message={index.message} /> : null}
      {game.state === "error" ? <Failed message={game.message} /> : null}
      {index.state === "loading" || (summary && game.state === "loading") ? <Loading what="the game" /> : null}
      {summary && game.state === "ready" ? <Replay key={summary.id} game={game.value} summary={summary} route={route} go={go} /> : null}
    </div>
  );
}

function GamePicker({ index, current, go }: { index: GameIndex; current: GameSummary; go: (next: Route) => void }) {
  return (
    <label className="flex min-w-0 items-center gap-2 text-sm text-stone-400">
      <span className="sr-only sm:not-sr-only">Game</span>
      <select
        value={current.id}
        onChange={(event) => go({ page: "viewer", game: event.target.value, tick: null, moment: null, claim: null })}
        className="min-w-0 max-w-[16rem] rounded-md border border-rim bg-panel px-2 py-1.5 text-stone-100"
      >
        {index.games.map((g) => (
          <option key={g.id} value={g.id}>
            Episode {g.seed} · {g.reference_bots ? "bots" : "models"} · {g.winner === "traitor" ? "traitors win" : "Faithful win"}
            {g.twist ? " · twist" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Replay({
  game,
  summary,
  route,
  go,
}: {
  game: Game;
  summary: GameSummary;
  route: ViewerRoute;
  go: (next: Route, how?: "push" | "replace") => void;
}) {
  const feed = game.feed;
  const plan = useMemo(() => planFor(feed.game.castle_id, feed.rooms), [feed]);
  const timeline = useMemo(() => tokenTimeline(feed), [feed]);
  const first = useMemo(() => railOrder(feed.moments)[0] ?? null, [feed]);
  const [claimId, setClaimId] = useState<string | null>(() => game.claim(route.claim)?.id ?? null);
  const opened = game.claim(claimId);
  const [momentId, setMomentId] = useState<string | null>(() => {
    if (feed.moments.some((m) => m.id === route.moment)) return route.moment;
    const own = opened ? feed.moments.find((m) => m.verdict?.claim === opened.id || m.claim === opened.seq) : undefined;
    return own?.id ?? first?.id ?? null;
  });
  const moment = feed.moments.find((m) => m.id === momentId) ?? null;
  const [tick, setTick] = useState(() =>
    Math.min(route.tick ?? opened?.tick ?? (moment ? momentTick(game, moment) : 0), game.lastTick),
  );
  const [playing, setPlaying] = useState(false);
  const [focus, setFocus] = useState<number | null>(opened?.seq ?? moment?.claim ?? null);
  const [style, setStyle] = useState<NameStyle>(() => defaultStyle(feed));
  const [roles, setRoles] = useState(true);
  const [suspect, setSuspect] = useState<string>(() => moment?.seats[0] ?? feed.seats[0].id);
  const naming = useMemo(() => Naming.of(feed, style), [feed, style]);
  const botGame = feed.game.reference_bots;

  useEffect(() => {
    if (!playing) return;
    if (tick >= game.lastTick) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => setTick((t) => Math.min(t + 1, game.lastTick)), 650);
    return () => window.clearTimeout(timer);
  }, [playing, tick, game.lastTick]);

  useEffect(() => {
    const timer = window.setTimeout(
      () => go({ page: "viewer", game: summary.id, tick, moment: momentId, claim: claimId }, "replace"),
      playing ? 1200 : 150,
    );
    return () => window.clearTimeout(timer);
  }, [tick, momentId, claimId, summary.id, go, playing]);

  const focused = focus !== null ? game.event(focus) : null;
  const selectMoment = (m: FeedMoment) => {
    setMomentId(m.id);
    setPlaying(false);
    setTick(momentTick(game, m));
    setFocus(m.claim ?? m.truth[0] ?? m.anchor);
    setSuspect(m.seats[0] ?? suspect);
  };
  const jump = (event: FeedEvent) => {
    setPlaying(false);
    setTick(event.tick);
    setFocus(event.seq);
  };
  const openReceipt = (id: string) => {
    const claim = game.claim(id);
    if (!claim) return;
    setClaimId(id);
    setPlaying(false);
    setTick(claim.tick);
    setFocus(claim.seq);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const tokens = tokensAt(timeline, tick);
  const happening = feed.events.filter((e) => e.tick === tick && !ROUTINE.has(e.type));
  const title = titleLine(game, style);

  return (
    <>
      {botGame ? (
        <div className="sticky top-0 z-20">
          <BotBanner />
        </div>
      ) : null}
      <main className="mx-auto grid max-w-7xl grid-cols-1 gap-4 px-4 pb-16 pt-3 sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-2xl font-black tracking-tight sm:text-3xl">
              Episode {feed.game.seed}
              {title ? <span className="font-serif font-normal italic text-stone-300"> · “{title}”</span> : null}
            </h1>
            <p className="text-sm text-stone-400">
              {feed.game.castle}. {feed.seats.length} players, {feed.game.days} days.{" "}
              {feed.game.winner === "traitor" ? "The traitors won." : feed.game.winner === "faithful" ? "The Faithful won." : ""}{" "}
              {summary.moments} moments
              {feed.game.fact_checked ? `, ${feed.claims.length} claims, every one checked against the tape.` : ". Nothing's been fact-checked yet."}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <div role="group" aria-label="Names" className="inline-flex rounded-full border border-rim bg-panel p-0.5">
              {(["character", "model"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={style === option}
                  onClick={() => setStyle(option)}
                  className={`rounded-full px-3 py-1 ${style === option ? "bg-stone-100 font-semibold text-ink" : "text-stone-300"}`}
                >
                  {option === "character" ? "Characters" : "Models"}
                </button>
              ))}
            </div>
            <label className="inline-flex items-center gap-1.5 text-stone-300">
              <input type="checkbox" checked={roles} onChange={(e) => setRoles(e.target.checked)} className="accent-traitor" />
              Show roles
            </label>
          </div>
        </div>

        {opened ? (
          <ClaimReceipt
            game={game}
            gameId={summary.id}
            claim={opened}
            style={style}
            naming={naming}
            onClose={() => setClaimId(null)}
            onJump={jump}
          />
        ) : null}

        <HighlightsRail moments={feed.moments} selected={momentId} style={style} onSelect={selectMoment} />

        <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <Panel>
            <CastleMap
              game={game}
              plan={plan}
              tokens={tokens}
              style={style}
              roles={roles}
              focus={{ room: focused?.room ?? null, actors: focused?.actor ? [focused.actor] : [] }}
            />
            <MapKey roles={roles} />
            <Scrubber game={game} tick={tick} playing={playing} moment={moment} onTick={(t) => setTick(t)} onPlay={setPlaying} />
          </Panel>
          <div className="grid min-w-0 grid-cols-1 content-start gap-4">
            <Panel title={`At tick ${tick} · ${when(feed.positions[tick].day, feed.positions[tick].phase)}`}>
              {happening.length === 0 ? <p className="text-sm text-stone-500">Nothing out of the ordinary.</p> : null}
              <ul className="grid max-h-72 gap-1 overflow-y-auto pr-1 text-sm">
                {happening.map((event) => (
                  <li key={event.seq}>
                    <button
                      type="button"
                      onClick={() => setFocus(event.seq)}
                      className={`w-full rounded-sm px-2 py-1 text-left hover:bg-white/5 ${focus === event.seq ? "bg-candle/10 text-candle" : "text-stone-300"}`}
                    >
                      {eventText(event, style)}
                      {event.quote && event.actor ? <span className="text-stone-400"> “{naming.words(event.quote)}”</span> : null}
                    </button>
                  </li>
                ))}
              </ul>
            </Panel>
            <Legend game={game} tokens={tokens} style={style} roles={roles} />
          </div>
        </div>

        {moment ? (
          <MomentPanel game={game} moment={moment} style={style} naming={naming} focus={focus} onJump={jump} onReceipt={openReceipt} />
        ) : null}

        <EvidenceBoard
          game={game}
          suspect={suspect}
          style={style}
          naming={naming}
          roles={roles}
          onSuspect={setSuspect}
          onJump={jump}
          onMoment={selectMoment}
        />
      </main>
    </>
  );
}

/** Where to open a moment: at its claim, the words the truth is set beside. */
export function momentTick(game: Game, moment: FeedMoment): number {
  return game.event(moment.claim ?? moment.anchor).tick;
}

function Legend({ game, tokens, style, roles }: { game: Game; tokens: ReturnType<typeof tokensAt>; style: NameStyle; roles: boolean }) {
  const labels = initials(game);
  return (
    <Panel title="Who is where">
      <ul className="grid grid-cols-1 gap-x-3 gap-y-1 text-sm min-[420px]:grid-cols-2 lg:grid-cols-1">
        {tokens.map((token) => {
          const fill = garmentColour(token.garment);
          const where = !token.alive
            ? "dead"
            : token.room
              ? game.roomName(token.room)
              : `→ ${game.roomName(token.destination)}`;
          return (
            <li key={token.id} className={`flex min-w-0 items-center gap-2 ${token.alive ? "" : "opacity-50"}`}>
              <span
                aria-hidden
                className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px] font-black"
                style={{ background: fill ?? "transparent", border: fill ? "none" : "1px dashed #888", color: token.garment === "dark shawl" ? "#f5ecd9" : "#14161a" }}
              >
                {labels.get(token.id)}
              </span>
              <SeatName game={game} id={token.id} style={style} className="min-w-0 truncate text-stone-200" />
              {roles && token.traitor ? <span className="h-2 w-2 shrink-0 rounded-full bg-traitor" title="traitor" /> : null}
              <span className="ml-auto truncate text-xs text-stone-500">
                {where}
                {token.asleep && token.alive ? " · asleep" : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
