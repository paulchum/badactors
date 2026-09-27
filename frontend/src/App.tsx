import { useEffect, useMemo, useRef, useState } from "react";

import { apiFetch, ownerToken } from "./api";
import { WorldPage } from "./WorldPage";

import { LeaderboardPage, type LeaderboardPayload } from "./LeaderboardPage";

type Visibility = "PUBLIC" | "TRAITORS" | "AUDIENCE" | "HOST";
type Role = "FAITHFUL" | "TRAITOR";

export type GameEvent = {
  id: number;
  game_id: string;
  ts: string;
  phase: string;
  type: string;
  payload: Record<string, unknown>;
  visibility: Visibility;
};

type ActivityRow = {
  key: string;
  block: number;
  phase: string;
  playerId: string;
  publicLines: string[];
  diaryLines: string[];
  actions: string[];
  reasoningTrace?: string;
};

type RevealMoment = {
  id: number;
  type: "BANISHMENT" | "BREAKFAST" | "GAME_OVER";
  title: string;
  detail: string;
  playerId?: string;
  role?: Role;
};

type ConclaveItem = {
  id: number;
  traitorId?: string;
  targetId?: string;
  text: string;
};

type ShareMoment = {
  id: string;
  eventId: number;
  title: string;
  signal: string;
  reason: string;
  playerId: string;
  targetId?: string;
  phase: string;
  publicLine: string;
  diaryLine: string;
  score: number;
  clipEvents: GameEvent[];
};

type PlayerProfile = {
  id: string;
  role?: Role;
  alive: boolean;
  tellScore: number;
  favorite: boolean;
};

type BluffRow = {
  model: string;
  mu: number;
  sigma: number;
  ordinal: number;
  games: number;
};

type SeasonGameSummary = {
  index: number;
  game_id: string;
  rng_seed: number;
  winning_role: Role;
  reason: string;
  rounds: number;
  traitor_ids: string[];
  alive_ids: string[];
  banished_ids: string[];
  murdered_ids: string[];
  event_count: number;
};

type SeasonAgentSummary = {
  player_id: string;
  model: string;
  games: number;
  wins: number;
  traitor_games: number;
  faithful_games: number;
  survived_wins: number;
  banishments: number;
  murders: number;
  final_memory_entries: number;
  final_memory_throttled: boolean;
  final_token_budget: number;
  final_thinking: boolean;
};

type SeasonRelationship = {
  source_id: string;
  target_id: string;
  trust_level: number;
  suspicion_level: number;
  observations: number;
};

type SeasonPayload = {
  season_id: string;
  games_requested: number;
  games_completed: number;
  games: SeasonGameSummary[];
  agents: SeasonAgentSummary[];
  relationships: SeasonRelationship[];
  bluff: {
    deception: BluffRow[];
    detection: BluffRow[];
  };
};

export type ViewState = {
  events: GameEvent[];
  rows: ActivityRow[];
  reveals: RevealMoment[];
  conclave: ConclaveItem[];
  roleByPlayer: Record<string, Role>;
  currentPhase: string;
  gameOver?: string;
};

type AppStatus = "idle" | "live" | "replayed" | "error";
type AppView = "game" | "leaderboard" | "season" | "world";

const emptyLeaderboard: LeaderboardPayload = {
  agents: [],
  creators: []
};

const demoPlayerIds = ["p1", "p2", "p3", "p4", "p5", "p6"];

const demoGame = {
  rng_seed: 13,
  traitor_count: 2,
  memory_budget: 100,
  token_budget: 200,
  agents: {
    "mock-alpha": { id: "p1" },
    "mock-bravo": { id: "p2" },
    "mock-charlie": { id: "p3" },
    "mock-delta": { id: "p4" },
    "mock-echo": { id: "p5" },
    "mock-foxtrot": { id: "p6" }
  }
};

const sseEventTypes = [
  "PhaseAdvanced",
  "PublicSpeech",
  "DiaryEntry",
  "VoteCast",
  "VoteTallied",
  "PlayerBanished",
  "MurderNomination",
  "PlayerMurdered",
  "NoMurder",
  "EndgameChoice",
  "GameOver"
];

export function App() {
  const [gameId, setGameId] = useState(initialGameId);
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [status, setStatus] = useState<AppStatus>("idle");
  const [error, setError] = useState("");
  const [view, setView] = useState<AppView>("world");
  const [leaderboard, setLeaderboard] = useState(emptyLeaderboard);
  const [season, setSeason] = useState<SeasonPayload | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const state = useMemo(() => reduceEvents(events), [events]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const sharedGame = new URLSearchParams(window.location.search).get("game");
    if (sharedGame) {
      void loadGameRecord(sharedGame);
    }
  }, []);

  function closeStream() {
    sourceRef.current?.close();
    sourceRef.current = null;
  }

  async function startMockGame() {
    closeStream();
    setError("");
    setEvents([]);
    const nextGameId = `ui-${Date.now()}`;
    setGameId(nextGameId);
    setStatus("live");
    setView("game");
    try {
      const response = await apiFetch("/games", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...demoGame, game_id: nextGameId })
      });
      if (!response.ok) {
        throw new Error(`Start failed: ${response.status}`);
      }
      connectStream(nextGameId);
    } catch (caught) {
      setStatus("error");
      setError(caught instanceof Error ? caught.message : "Start failed.");
    }
  }

  async function startMockSeason() {
    closeStream();
    setError("");
    const nextSeasonId = `season-${Date.now()}`;
    setStatus("live");
    setView("season");
    try {
      const response = await apiFetch("/seasons", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...demoGame,
          season_id: nextSeasonId,
          games: 12
        })
      });
      if (!response.ok) {
        throw new Error(`Season failed: ${response.status}`);
      }
      const payload = (await response.json()) as SeasonPayload;
      setSeason(payload);
      setStatus("replayed");
    } catch (caught) {
      setStatus("error");
      setError(caught instanceof Error ? caught.message : "Season failed.");
    }
  }

  function connectStream(nextGameId = gameId) {
    closeStream();
    if (ownerToken()) { void loadGameRecord(nextGameId); return; }
    const source = new EventSource(`/games/${nextGameId}/stream`);
    sourceRef.current = source;
    const handleEvent = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as GameEvent;
      setEvents((current) =>
        current.some((existing) => existing.id === event.id)
          ? current
          : [...current, event]
      );
      if (event.type === "GameOver") {
        source.close();
      }
    };
    source.onmessage = handleEvent;
    for (const eventType of sseEventTypes) {
      source.addEventListener(eventType, handleEvent);
    }
    source.onerror = () => {
      source.close();
      if (status !== "replayed") {
        setStatus("error");
        setError("Stream disconnected.");
      }
    };
  }

  async function replayGame() {
    closeStream();
    setError("");
    try {
      const response = await apiFetch(`/games/${gameId}/replay`, { method: "POST" });
      if (!response.ok) {
        throw new Error(`Replay failed: ${response.status}`);
      }
      const payload = (await response.json()) as { events: GameEvent[] };
      setEvents(payload.events);
      setStatus("replayed");
      setView("game");
    } catch (caught) {
      setStatus("error");
      setError(caught instanceof Error ? caught.message : "Replay failed.");
    }
  }

  async function loadGameRecord(nextGameId: string) {
    closeStream();
    setError("");
    setGameId(nextGameId);
    try {
      const response = await apiFetch(`/games/${nextGameId}`);
      if (!response.ok) {
        throw new Error(`Game load failed: ${response.status}`);
      }
      const payload = (await response.json()) as { events: GameEvent[] };
      setEvents(payload.events);
      setStatus("replayed");
      setView("game");
    } catch (caught) {
      setStatus("error");
      setError(caught instanceof Error ? caught.message : "Game load failed.");
    }
  }

  async function loadLeaderboard() {
    setError("");
    try {
      const response = await apiFetch("/leaderboard");
      if (!response.ok) {
        throw new Error(`Leaderboard failed: ${response.status}`);
      }
      setLeaderboard((await response.json()) as LeaderboardPayload);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Leaderboard failed."
      );
    }
  }

  async function showLeaderboard() {
    setView("leaderboard");
    await loadLeaderboard();
  }

  return (
    <div className="app-shell">
      <TopBar
        gameId={gameId}
        status={status}
        view={view}
        onGameId={setGameId}
        onStart={startMockGame}
        onSeason={startMockSeason}
        onReplay={replayGame}
        onGameView={() => setView("game")}
        onLeaderboard={showLeaderboard}
        onSeasonView={() => setView("season")}
        onWorldView={() => { closeStream(); setView("world"); }}
      />
      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}
      {view === "world" ? (
        <WorldPage onArena={id => void loadGameRecord(id)} />
      ) : view === "leaderboard" ? (
        <LeaderboardPage
          leaderboard={leaderboard}
          onRefresh={() => {
            void loadLeaderboard();
          }}
        />
      ) : view === "season" ? (
        <SeasonPage season={season} />
      ) : (
        <SpectatorBoard gameId={gameId} state={state} status={status} />
      )}
    </div>
  );
}

function TopBar({
  gameId,
  status,
  view,
  onGameId,
  onStart,
  onSeason,
  onReplay,
  onGameView,
  onLeaderboard,
  onSeasonView,
  onWorldView
}: {
  gameId: string;
  status: AppStatus;
  view: AppView;
  onGameId: (value: string) => void;
  onStart: () => void;
  onSeason: () => void;
  onReplay: () => void;
  onGameView: () => void;
  onLeaderboard: () => void;
  onSeasonView: () => void;
  onWorldView: () => void;
}) {
  return (
    <header className={`topbar ${view === "world" ? "topbar-world" : ""}`}>
      <div className="brand-lockup" aria-label="Bad Actors">
        <span>BAD</span>
        <span className="brand-mask">ACTORS</span>
      </div>
      <div className="episode-meta" aria-label="Episode status">
        <span className="live-dot" />
        <span>{view === "world" ? "LIVING WORLDS" : status === "replayed" ? "REPLAY EPISODE" : "ARENA"}</span>
      </div>
      {view !== "world" ? <label className="game-field" htmlFor="game-id">
        <span>Game ID</span>
        <input
          id="game-id"
          value={gameId}
          onChange={(event) => onGameId(event.target.value)}
        />
      </label> : null}
      <nav className="topbar-actions" aria-label="Spectator controls">
        <button className={`control-button ${view === "world" ? "is-active" : ""}`} onClick={onWorldView} type="button">Worlds</button>
        {view !== "world" ? <><button className="control-button" onClick={onStart} type="button">
          Start mock game
        </button>
        <button className="control-button" onClick={onSeason} type="button">
          Start season
        </button>
        <button className="control-button" onClick={onReplay} type="button">
          Replay
        </button></> : null}
        <button
          className={`control-button ${view === "game" ? "is-active" : ""}`}
          onClick={onGameView}
          type="button"
        >
          Table
        </button>
        <button
          className={`control-button ${
            view === "leaderboard" ? "is-active" : ""
          }`}
          onClick={onLeaderboard}
          type="button"
        >
          Leaderboard
        </button>
        <button
          className={`control-button ${view === "season" ? "is-active" : ""}`}
          onClick={onSeasonView}
          type="button"
        >
          Season
        </button>
      </nav>
    </header>
  );
}

function SeasonPage({ season }: { season: SeasonPayload | null }) {
  if (!season) {
    return (
      <main className="season-board">
        <section className="season-hero">
          <div>
            <div className="section-kicker">SEASON MODE</div>
            <h1>No season loaded.</h1>
          </div>
          <div className="hero-stats">
            <Stat label="Games" value="0" />
            <Stat label="Agents" value="0" />
            <Stat label="Reads" value="0" />
          </div>
        </section>
      </main>
    );
  }

  const traitorWins = season.games.filter(
    (game) => game.winning_role === "TRAITOR"
  ).length;
  const faithfulWins = season.games_completed - traitorWins;
  const topAgent = [...season.agents].sort(
    (left, right) => right.wins - left.wins || left.player_id.localeCompare(right.player_id)
  )[0];
  const strongestRelationship = [...season.relationships].sort(
    (left, right) =>
      right.observations - left.observations ||
      right.suspicion_level - left.suspicion_level ||
      left.source_id.localeCompare(right.source_id)
  )[0];

  return (
    <main className="season-board">
      <section className="season-hero">
        <div>
          <div className="section-kicker">SEASON MODE</div>
          <h1>{season.season_id}</h1>
        </div>
        <div className="hero-stats">
          <Stat
            label="Games"
            value={`${season.games_completed}/${season.games_requested}`}
          />
          <Stat label="Traitor wins" value={String(traitorWins)} />
          <Stat label="Faithful wins" value={String(faithfulWins)} />
          <Stat
            label="Top agent"
            value={topAgent ? `${topAgent.player_id} (${topAgent.wins})` : "None"}
          />
        </div>
      </section>
      <section className="season-grid">
        <SeasonAgentTable agents={season.agents} />
        <SeasonRelationshipTable
          relationships={season.relationships}
          strongest={strongestRelationship}
        />
        <SeasonGameTable games={season.games} />
        <SeasonBluffPanel bluff={season.bluff} />
      </section>
    </main>
  );
}

function SeasonAgentTable({ agents }: { agents: SeasonAgentSummary[] }) {
  return (
    <section className="season-panel wide">
      <div className="table-heading">
        <h2>Agent resources</h2>
        <span>{agents.length}</span>
      </div>
      <div className="season-table-wrap">
        <table className="season-table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Model</th>
              <th>Wins</th>
              <th>Survived wins</th>
              <th>Outs</th>
              <th>Memory</th>
              <th>Budget</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => (
              <tr key={agent.player_id}>
                <td className="font-black">{agent.player_id}</td>
                <td>{agent.model}</td>
                <td>{agent.wins}/{agent.games}</td>
                <td>{agent.survived_wins}</td>
                <td>{agent.banishments + agent.murders}</td>
                <td>
                  {agent.final_memory_entries}
                  {agent.final_memory_throttled ? " throttled" : ""}
                </td>
                <td>
                  {agent.final_token_budget}
                  {agent.final_thinking ? " + thinking" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SeasonRelationshipTable({
  relationships,
  strongest
}: {
  relationships: SeasonRelationship[];
  strongest?: SeasonRelationship;
}) {
  const rows = [...relationships]
    .sort(
      (left, right) =>
        right.observations - left.observations ||
        right.suspicion_level - left.suspicion_level ||
        left.source_id.localeCompare(right.source_id)
    )
    .slice(0, 10);

  return (
    <section className="season-panel">
      <div className="table-heading">
        <h2>Relationship memory</h2>
        <span>
          {strongest
            ? `${strongest.source_id} -> ${strongest.target_id}`
            : "No reads"}
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="muted-copy">No relationship reads recorded.</p>
      ) : (
        <div className="relationship-list">
          {rows.map((relationship) => (
            <div
              className="relationship-row"
              key={`${relationship.source_id}:${relationship.target_id}`}
            >
              <div>
                <strong>
                  {relationship.source_id}
                  {" -> "}
                  {relationship.target_id}
                </strong>
                <span>{relationship.observations} observations</span>
              </div>
              <div className="relationship-bars">
                <span>
                  Trust
                  <b style={{ width: `${relationship.trust_level * 10}%` }} />
                </span>
                <span>
                  Suspicion
                  <b style={{ width: `${relationship.suspicion_level * 10}%` }} />
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function SeasonGameTable({ games }: { games: SeasonGameSummary[] }) {
  return (
    <section className="season-panel">
      <div className="table-heading">
        <h2>Games</h2>
        <span>{games.length}</span>
      </div>
      <div className="season-game-list">
        {games.map((game) => (
          <article className="season-game-row" key={game.game_id}>
            <div>
              <strong>{game.game_id}</strong>
              <span>Seed {game.rng_seed} · {game.event_count} events</span>
            </div>
            <RolePill role={game.winning_role} />
            <p>
              Round {game.rounds} · banished {game.banished_ids.length} · murdered{" "}
              {game.murdered_ids.length}
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}

function SeasonBluffPanel({
  bluff
}: {
  bluff: SeasonPayload["bluff"];
}) {
  return (
    <section className="season-panel">
      <div className="table-heading">
        <h2>BLUFF standings</h2>
        <span>D / D</span>
      </div>
      <div className="bluff-season-grid">
        <BluffList title="Deception" rows={bluff.deception} />
        <BluffList title="Detection" rows={bluff.detection} />
      </div>
    </section>
  );
}

function BluffList({ title, rows }: { title: string; rows: BluffRow[] }) {
  return (
    <div className="bluff-list">
      <h3>{title}</h3>
      {rows.slice(0, 5).map((row, index) => (
        <div className="bluff-standing" key={row.model}>
          <span>{index + 1}</span>
          <strong>{row.model}</strong>
          <b>{Math.round(row.ordinal)}</b>
        </div>
      ))}
      {rows.length === 0 ? <p className="muted-copy">No standings yet.</p> : null}
    </div>
  );
}

export function SpectatorBoard({
  state,
  status,
  gameId = state.events[0]?.game_id ?? "fixture"
}: {
  state: ViewState;
  status: AppStatus;
  gameId?: string;
}) {
  const moments = useMemo(() => buildShareMoments(state), [state]);
  const [selectedMomentId, setSelectedMomentId] = useState(initialMomentId);
  const [predictionId, setPredictionId] = useState("");
  const [favoriteId, setFavoriteId] = useState("");
  const selectedMoment =
    moments.find(
      (moment) =>
        moment.id === selectedMomentId ||
        String(moment.eventId) === selectedMomentId?.replace("event-", "")
    ) ?? moments[0];
  const players = useMemo(
    () => buildPlayerProfiles(state, favoriteId),
    [favoriteId, state]
  );
  const phase = formatPhase(state.currentPhase || "LOBBY");
  const latestReveal = state.reveals[state.reveals.length - 1];

  useEffect(() => {
    syncMomentMeta(gameId, selectedMoment);
  }, [gameId, selectedMoment]);

  return (
    <main className="spectator-board">
      <CastRail
        onFavorite={setFavoriteId}
        players={players}
        selectedId={selectedMoment?.playerId}
      />
      <section className="stage-column" aria-label="Bad Actors live episode">
        <EpisodeHero
          eventCount={state.events.length}
          latestReveal={latestReveal}
          phase={phase}
          status={status}
        />
        <BetrayalStage
          gameId={gameId}
          moment={selectedMoment}
          phase={phase}
        />
        <ReplayStrip
          events={state.events}
          moments={moments}
          onSelectMoment={setSelectedMomentId}
          selectedMomentId={selectedMoment?.id}
        />
        <TranscriptPanel state={state} />
      </section>
      <LivePulseRail
        gameId={gameId}
        moments={moments}
        onPrediction={setPredictionId}
        onSelectMoment={setSelectedMomentId}
        players={players}
        predictionId={predictionId}
        selectedMomentId={selectedMoment?.id}
        state={state}
      />
    </main>
  );
}

function EpisodeHero({
  eventCount,
  latestReveal,
  phase,
  status
}: {
  eventCount: number;
  latestReveal?: RevealMoment;
  phase: string;
  status: AppStatus;
}) {
  return (
    <section className="episode-hero" aria-label="Episode summary">
      <div>
        <div className="section-kicker">ROUND TABLE</div>
        <h1>Public certainty. Private reversal.</h1>
      </div>
      <div className="hero-stats">
        <Stat label="Mode" value={status === "replayed" ? "Replay" : "Live"} />
        <Stat label="Phase" value={phase} />
        <Stat label="Events" value={String(eventCount)} />
        <Stat label="Latest" value={latestReveal?.title ?? "Awaiting reveal"} />
      </div>
    </section>
  );
}

function BetrayalStage({
  gameId,
  moment,
  phase
}: {
  gameId: string;
  moment?: ShareMoment;
  phase: string;
}) {
  if (!moment) {
    return (
      <section className="betrayal-stage is-empty" aria-label="Betrayal moment">
        <div className="stage-phase">{phase}</div>
        <div className="empty-stage">
          <div className="empty-title">Awaiting the first public/private split.</div>
          <p>
            Start or replay a game to surface the moment where the table hears one
            line and the audience sees the plan underneath.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="betrayal-stage" aria-label="Betrayal moment">
      <div className="stage-phase">{phase}</div>
      <div className="quote-stack">
        <QuotePanel
          label="PUBLIC"
          playerId={moment.playerId}
          text={moment.publicLine}
          tone="public"
        />
        <QuotePanel
          label="DIARY"
          playerId={moment.playerId}
          text={moment.diaryLine}
          tone="diary"
        />
      </div>
      <div className="contradiction-badge">CONTRADICTION</div>
      <TellMeter score={moment.score} />
      <div className="share-actions" aria-label="Share this moment">
        <button
          className="share-button"
          onClick={() => {
            void copyMomentCard(moment, gameId);
          }}
          type="button"
        >
          Copy card
        </button>
        <button
          className="share-button"
          onClick={() => {
            void shareMoment(moment, gameId);
          }}
          type="button"
        >
          Share URL
        </button>
        <button
          className="share-button"
          onClick={() => {
            void downloadShareCardPng(moment, gameId);
          }}
          type="button"
        >
          PNG card
        </button>
        <button
          className="share-button"
          onClick={() => downloadClipProps(moment, gameId)}
          type="button"
        >
          Export clip
        </button>
      </div>
    </section>
  );
}

function QuotePanel({
  label,
  playerId,
  text,
  tone
}: {
  label: string;
  playerId: string;
  text: string;
  tone: "public" | "diary";
}) {
  return (
    <article className={`quote-panel ${tone}`}>
      <div className="player-token" aria-hidden="true">
        {playerId.toUpperCase()}
      </div>
      <div>
        <div className="quote-label">{label}</div>
        <p>
          <strong>{playerId}</strong> {tone === "public" ? "said: " : "diary: "}
          {humanizeLine(text)}
        </p>
      </div>
    </article>
  );
}

function TellMeter({ score }: { score: number }) {
  return (
    <div className="tell-meter" aria-label={`Tell meter ${score} percent`}>
      <div className="tell-copy">
        <span>Suspicion / tell meter</span>
        <strong>{score}%</strong>
      </div>
      <div className="meter-track">
        <div className="meter-fill" style={{ width: `${score}%` }} />
      </div>
      <div className="meter-scale">
        <span>0%</span>
        <span>50%</span>
        <span>100%</span>
      </div>
    </div>
  );
}

function CastRail({
  onFavorite,
  players,
  selectedId
}: {
  onFavorite: (playerId: string) => void;
  players: PlayerProfile[];
  selectedId?: string;
}) {
  return (
    <aside className="cast-rail" aria-label="Cast">
      <div className="rail-heading">
        <h2>Cast</h2>
        <span>{players.length} / {players.length}</span>
      </div>
      <div className="cast-list">
        {players.map((player) => (
          <article
            className={`cast-card ${
              selectedId === player.id ? "is-selected" : ""
            }`}
            key={player.id}
          >
            <div className={`avatar-ring ${player.role?.toLowerCase() ?? ""}`}>
              {player.id.toUpperCase()}
            </div>
            <div className="cast-main">
              <div className="cast-name">{player.id}</div>
              <RolePill role={player.role} />
              <div className="alive-state">
                <span className={player.alive ? "alive-dot" : "dead-dot"} />
                {player.alive ? "Alive" : "Out"}
              </div>
            </div>
            <button
              aria-label={`Favorite ${player.id}`}
              aria-pressed={player.favorite}
              className={`favorite-button ${player.favorite ? "is-on" : ""}`}
              onClick={() => onFavorite(player.favorite ? "" : player.id)}
              type="button"
            >
              {player.favorite ? "★" : "☆"}
            </button>
            <div className="mini-meter" aria-label={`Tell ${player.tellScore}%`}>
              <span style={{ width: `${player.tellScore}%` }} />
            </div>
          </article>
        ))}
      </div>
    </aside>
  );
}

function LivePulseRail({
  gameId,
  moments,
  onPrediction,
  onSelectMoment,
  players,
  predictionId,
  selectedMomentId,
  state
}: {
  gameId: string;
  moments: ShareMoment[];
  onPrediction: (playerId: string) => void;
  onSelectMoment: (momentId: string) => void;
  players: PlayerProfile[];
  predictionId: string;
  selectedMomentId?: string;
  state: ViewState;
}) {
  const traitors = Object.values(state.roleByPlayer).filter(
    (role) => role === "TRAITOR"
  ).length;
  const faithful = Object.values(state.roleByPlayer).filter(
    (role) => role === "FAITHFUL"
  ).length;

  return (
    <aside className="live-pulse" aria-label="Live pulse">
      <section className="pulse-card">
        <div className="rail-heading">
          <h2>Live pulse</h2>
          <span>Live</span>
        </div>
        <div className="phase-card">
          <span>Phase</span>
          <strong>{formatPhase(state.currentPhase || "LOBBY")}</strong>
          <p>{phaseHint(state.currentPhase)}</p>
        </div>
      </section>
      <section className="pulse-card">
        <h2>Who's the Traitor?</h2>
        <div className="prediction-list">
          {players.map((player) => {
            const percent = predictionPercent(player, predictionId);
            return (
              <button
                aria-pressed={predictionId === player.id}
                className="prediction-row"
                key={player.id}
                onClick={() => onPrediction(player.id)}
                type="button"
              >
                <span>{player.id}</span>
                <span className="prediction-bar">
                  <span style={{ width: `${percent}%` }} />
                </span>
                <strong>{percent}%</strong>
              </button>
            );
          })}
        </div>
      </section>
      <section className="pulse-card">
        <h2>Latest highlights</h2>
        <div className="highlight-list">
          {state.conclave.slice(-2).map((item) => (
            <div className="highlight-row" key={item.id}>
              <span>Conclave</span>
              <p>{item.text}</p>
            </div>
          ))}
          {state.reveals.slice(-2).map((moment) => (
            <div className="highlight-row" key={`reveal-${moment.id}`}>
              <span>{moment.title}</span>
              <p>{moment.detail}</p>
            </div>
          ))}
          {state.conclave.length === 0 && state.reveals.length === 0 ? (
            <p className="muted-copy">No reveal beats yet.</p>
          ) : null}
        </div>
      </section>
      <section className="pulse-card">
        <div className="rail-heading">
          <h2>Share moments</h2>
          <span>{moments.length}</span>
        </div>
        <div className="moment-list">
          {moments.length === 0 ? (
            <p className="muted-copy">The first diary/public contradiction will land here.</p>
          ) : (
            moments.slice(0, 4).map((moment, index) => (
              <button
                className={`moment-row ${
                  selectedMomentId === moment.id ? "is-active" : ""
                }`}
                key={moment.id}
                onClick={() => onSelectMoment(moment.id)}
                type="button"
              >
                <span>{index + 1}</span>
                <div>
                  <strong>{moment.title}</strong>
                  <p>{moment.reason}</p>
                </div>
                <b>{moment.score}</b>
              </button>
            ))
          )}
        </div>
      </section>
      <section className="pulse-card bluff-card">
        <h2>BLUFF leaderboard</h2>
        <div className="bluff-row">
          <span>Revealed Faithful</span>
          <strong>{faithful}</strong>
        </div>
        <div className="bluff-row">
          <span>Revealed Traitors</span>
          <strong>{traitors}</strong>
        </div>
        <div className="bluff-row">
          <span>Clip props</span>
          <strong>{gameId}</strong>
        </div>
      </section>
    </aside>
  );
}

function ReplayStrip({
  events,
  moments,
  onSelectMoment,
  selectedMomentId
}: {
  events: GameEvent[];
  moments: ShareMoment[];
  onSelectMoment: (momentId: string) => void;
  selectedMomentId?: string;
}) {
  const total = Math.max(1, events.length);

  return (
    <section className="replay-strip" aria-label="Event replay">
      <div className="replay-heading">
        <h2>Event replay</h2>
        <span>{events.length ? `${events.length} events` : "No events"}</span>
      </div>
      <div className="scrubber">
        {events.length === 0 ? (
          <span className="scrubber-empty" />
        ) : (
          events.map((event, index) => (
            <span
              className={`event-tick ${eventTone(event)}`}
              key={event.id}
              style={{ left: `${(index / total) * 100}%` }}
              title={`${event.id}: ${event.type}`}
            />
          ))
        )}
        {moments.map((moment) => {
          const eventIndex = Math.max(
            0,
            events.findIndex((event) => event.id === moment.eventId)
          );
          return (
            <button
              aria-label={`Select ${moment.title}`}
              className={`clip-marker ${
                selectedMomentId === moment.id ? "is-active" : ""
              }`}
              key={moment.id}
              onClick={() => onSelectMoment(moment.id)}
              style={{ left: `${(eventIndex / total) * 100}%` }}
              type="button"
            />
          );
        })}
      </div>
    </section>
  );
}

function TranscriptPanel({ state }: { state: ViewState }) {
  return (
    <section className="transcript-grid" aria-label="Full transcript">
      <Timeline events={state.events} />
      <ActivityTable state={state} />
    </section>
  );
}

function Timeline({ events }: { events: GameEvent[] }) {
  return (
    <aside className="timeline-panel">
      <h2>Event timeline</h2>
      <ol>
        {events.length === 0 ? (
          <li className="timeline-empty">No events streamed yet.</li>
        ) : (
          events.slice(-18).map((event) => (
            <li className="timeline-item" key={event.id}>
              <span className={`timeline-dot ${eventTone(event)}`} />
              <div className="timeline-index">#{event.id} {formatPhase(event.phase)}</div>
              <div className="timeline-type">{event.type}</div>
              <div className="timeline-text">{timelineText(event)}</div>
            </li>
          ))
        )}
      </ol>
    </aside>
  );
}

function ActivityTable({ state }: { state: ViewState }) {
  return (
    <section className="activity-panel">
      <div className="table-heading">
        <h2>Public table / private diary</h2>
        <span>{state.rows.length} turns</span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Player</th>
              <th>Alignment</th>
              <th>PUBLIC <span>(said)</span></th>
              <th>DIARY <span>(thought)</span></th>
            </tr>
          </thead>
          <tbody>
            {state.rows.length === 0 ? (
              <tr>
                <td className="empty-table" colSpan={4}>
                  No public or diary turns yet.
                </td>
              </tr>
            ) : (
              state.rows.map((row) => (
                <tr
                  className={rowTone(state.roleByPlayer[row.playerId])}
                  data-player={row.playerId}
                  key={row.key}
                >
                  <td>
                    <div className="table-player">{row.playerId}</div>
                    <div className="table-phase">{formatPhase(row.phase)}</div>
                  </td>
                  <td>
                    <RolePill role={state.roleByPlayer[row.playerId]} />
                  </td>
                  <td data-testid={`public-${row.playerId}`}>
                    {row.publicLines.map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                    {row.actions.map((action) => (
                      <p className="table-action" key={action}>
                        {action}
                      </p>
                    ))}
                  </td>
                  <td className="diary-cell" data-testid={`diary-${row.playerId}`}>
                    {row.diaryLines.map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                    {row.reasoningTrace ? (
                      <details className="reasoning-trace">
                        <summary>Raw reasoning</summary>
                        <pre>{row.reasoningTrace}</pre>
                      </details>
                    ) : null}
                  </td>
                </tr>
              ))
            )}
            {state.reveals.map((moment) => (
              <tr className="reveal-row" key={`reveal-${moment.id}`}>
                <td colSpan={2}>{moment.title}</td>
                <td colSpan={2}>{moment.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RolePill({ role }: { role?: Role }) {
  if (!role) {
    return <span className="role-pill unknown">Unknown</span>;
  }
  return <span className={`role-pill ${role.toLowerCase()}`}>{role}</span>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="hero-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function reduceEvents(events: GameEvent[]): ViewState {
  const rows = new Map<string, ActivityRow>();
  const reveals: RevealMoment[] = [];
  const conclave: ConclaveItem[] = [];
  const roleByPlayer: Record<string, Role> = {};
  let currentPhase = "";
  let block = 0;
  let gameOver: string | undefined;

  for (const event of events) {
    if (event.phase !== currentPhase) {
      currentPhase = event.phase;
      block += 1;
    }

    const playerId = stringPayload(event, "player_id");
    if (event.type === "PublicSpeech" && playerId) {
      rowFor(rows, block, event.phase, playerId).publicLines.push(
        stringPayload(event, "speech") ?? ""
      );
    }

    if (event.type === "DiaryEntry" && playerId) {
      const row = rowFor(rows, block, event.phase, playerId);
      row.diaryLines.push(stringPayload(event, "diary") ?? "");
      const trace = stringPayload(event, "reasoning_trace");
      if (trace) {
        row.reasoningTrace = trace;
      }
    }

    if (event.type === "VoteCast") {
      const voterId = stringPayload(event, "voter_id");
      const targetId = stringPayload(event, "target_id");
      if (voterId && targetId) {
        rowFor(rows, block, event.phase, voterId).actions.push(
          `Vote: ${targetId}`
        );
      }
    }

    if (event.type === "EndgameChoice" && playerId) {
      rowFor(rows, block, event.phase, playerId).actions.push(
        `Choice: ${stringPayload(event, "choice") ?? "unknown"}`
      );
    }

    if (event.type === "MurderNomination") {
      const traitorId = stringPayload(event, "traitor_id");
      const targetId = stringPayload(event, "target_id");
      if (traitorId) {
        roleByPlayer[traitorId] = "TRAITOR";
      }
      conclave.push({
        id: event.id,
        traitorId,
        targetId,
        text: `${traitorId ?? "A Traitor"} nominated ${targetId ?? "unknown"}.`
      });
      if (traitorId) {
        rowFor(rows, block, event.phase, traitorId).actions.push(
          `Nomination: ${targetId ?? "unknown"}`
        );
      }
    }

    if (event.type === "PlayerBanished" || event.type === "PlayerMurdered") {
      const revealedPlayer = stringPayload(event, "player_id");
      const role = rolePayload(event, "role");
      if (revealedPlayer && role) {
        roleByPlayer[revealedPlayer] = role;
      }
      reveals.push(revealFromEvent(event, revealedPlayer, role));
    }

    if (event.type === "GameOver") {
      const winner = stringPayload(event, "winning_role") ?? "Unknown";
      const reason = stringPayload(event, "reason") ?? "unknown";
      gameOver = `${winner} wins by ${reason}.`;
      reveals.push({
        id: event.id,
        type: "GAME_OVER",
        title: "Game over",
        detail: gameOver
      });
    }
  }

  return {
    events,
    rows: [...rows.values()],
    reveals,
    conclave,
    roleByPlayer,
    currentPhase,
    gameOver
  };
}

export function buildShareMoments(state: ViewState): ShareMoment[] {
  const moments: ShareMoment[] = [];

  for (const row of state.rows) {
    const publicLine = latest(row.publicLines);
    const diaryLine = latest(row.diaryLines);
    if (!publicLine || !diaryLine) {
      continue;
    }

    const eventId = latestEventId(state.events, row.playerId, row.phase);
    moments.push({
      id: `row-${row.key}`,
      eventId,
      title: "Public line meets private diary",
      signal: "public_diary_contradiction",
      reason: `${row.playerId} gave the table one read while the diary exposed the private read.`,
      playerId: row.playerId,
      targetId: firstMentionedPlayer(`${publicLine} ${diaryLine}`, allPlayerIds(state)),
      phase: row.phase,
      publicLine,
      diaryLine,
      score: contradictionScore(publicLine, diaryLine, row.actions),
      clipEvents: eventWindow(state.events, eventId)
    });
  }

  for (const conclave of state.conclave) {
    const traitorId = conclave.traitorId;
    if (!traitorId) {
      continue;
    }
    const row = [...state.rows]
      .reverse()
      .find((candidate) => candidate.playerId === traitorId);
    const publicLine = latest(row?.publicLines ?? []);
    const diaryLine = latest(row?.diaryLines ?? []);
    if (!publicLine && !diaryLine) {
      continue;
    }
    moments.push({
      id: `conclave-${conclave.id}`,
      eventId: conclave.id,
      title: "Conclave turns the table read",
      signal: "conclave_nomination",
      reason: `${traitorId} moved privately against ${conclave.targetId ?? "a target"}.`,
      playerId: traitorId,
      targetId: conclave.targetId,
      phase: "CONCLAVE",
      publicLine: publicLine ?? `${traitorId} kept the public table steady.`,
      diaryLine: diaryLine ?? conclave.text,
      score: 88,
      clipEvents: eventWindow(state.events, conclave.id)
    });
  }

  return uniqueMoments(moments).sort(
    (left, right) => right.score - left.score || left.eventId - right.eventId
  );
}

export function createShareCardSvg(moment: ShareMoment, gameId: string) {
  const publicLines = wrapSvgText(moment.publicLine, 44).slice(0, 3);
  const diaryLines = wrapSvgText(moment.diaryLine, 44).slice(0, 3);
  const phase = formatPhase(moment.phase);
  const reason = wrapSvgText(moment.reason, 58).slice(0, 2);

  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" role="img" aria-label="Bad Actors betrayal card">
  <rect width="1200" height="630" fill="#07090d"/>
  <rect x="28" y="26" width="1144" height="578" rx="16" fill="#0d1218" stroke="#2a313a"/>
  <text x="70" y="86" fill="#fff7ed" font-family="Inter, Arial, sans-serif" font-size="42" font-weight="900">BAD <tspan fill="#e13d45">ACTORS</tspan></text>
  <text x="70" y="126" fill="#d89b1d" font-family="Inter, Arial, sans-serif" font-size="20" font-weight="800">${escapeXml(gameId)} · ${escapeXml(phase)} · EVENT ${moment.eventId}</text>
  <rect x="70" y="158" width="1060" height="142" rx="10" fill="#0a1119" stroke="#2c7be5"/>
  <text x="104" y="205" fill="#76aef4" font-family="Inter, Arial, sans-serif" font-size="24" font-weight="900">PUBLIC · ${escapeXml(moment.playerId)}</text>
  ${publicLines
    .map(
      (line, index) =>
        `<text x="104" y="${252 + index * 34}" fill="#fff7ed" font-family="Inter, Arial, sans-serif" font-size="31" font-weight="800">${escapeXml(line)}</text>`
    )
    .join("")}
  <rect x="70" y="324" width="1060" height="146" rx="10" fill="#f3efe8" stroke="#d89b1d"/>
  <text x="104" y="371" fill="#8f1f2a" font-family="Inter, Arial, sans-serif" font-size="24" font-weight="900">DIARY · ${escapeXml(moment.playerId)}</text>
  ${diaryLines
    .map(
      (line, index) =>
        `<text x="104" y="${418 + index * 34}" fill="#111827" font-family="Inter, Arial, sans-serif" font-size="31" font-weight="900">${escapeXml(line)}</text>`
    )
    .join("")}
  <rect x="386" y="492" width="428" height="58" rx="8" fill="#31090d" stroke="#e13d45"/>
  <text x="438" y="532" fill="#ff6b73" font-family="Inter, Arial, sans-serif" font-size="30" font-weight="900">CONTRADICTION · ${moment.score}%</text>
  ${reason
    .map(
      (line, index) =>
        `<text x="70" y="${578 + index * 24}" fill="#cbd5e1" font-family="Inter, Arial, sans-serif" font-size="18" font-weight="700">${escapeXml(line)}</text>`
    )
    .join("")}
</svg>`;
}

function rowFor(
  rows: Map<string, ActivityRow>,
  block: number,
  phase: string,
  playerId: string
) {
  const key = `${block}:${phase}:${playerId}`;
  const existing = rows.get(key);
  if (existing) {
    return existing;
  }
  const row: ActivityRow = {
    key,
    block,
    phase,
    playerId,
    publicLines: [],
    diaryLines: [],
    actions: []
  };
  rows.set(key, row);
  return row;
}

function revealFromEvent(
  event: GameEvent,
  playerId: string | undefined,
  role: Role | undefined
): RevealMoment {
  if (event.type === "PlayerMurdered") {
    return {
      id: event.id,
      type: "BREAKFAST",
      title: "Breakfast reveal",
      playerId,
      role,
      detail: `${playerId ?? "A player"} was murdered and revealed as ${
        role ?? "unknown"
      }.`
    };
  }
  return {
    id: event.id,
    type: "BANISHMENT",
    title: "Banishment",
    playerId,
    role,
    detail: `${playerId ?? "A player"} was banished and revealed as ${
      role ?? "unknown"
    }.`
  };
}

function buildPlayerProfiles(state: ViewState, favoriteId: string) {
  return allPlayerIds(state).map((id) => ({
    id,
    role: state.roleByPlayer[id],
    alive: !state.reveals.some(
      (moment) => moment.playerId === id && moment.type !== "GAME_OVER"
    ),
    tellScore: playerTellScore(id, state),
    favorite: favoriteId === id
  }));
}

function playerTellScore(playerId: string, state: ViewState) {
  const role = state.roleByPlayer[playerId];
  const nominations = state.conclave.filter(
    (item) => item.traitorId === playerId
  ).length;
  const rowPressure = state.rows
    .filter((row) => row.playerId === playerId)
    .reduce((score, row) => {
      const hasSplit = row.publicLines.length > 0 && row.diaryLines.length > 0;
      return score + (hasSplit ? 13 : 0) + row.actions.length * 5;
    }, 0);
  const rolePressure = role === "TRAITOR" ? 34 : role === "FAITHFUL" ? 8 : 0;
  return Math.min(96, 12 + rolePressure + nominations * 22 + rowPressure);
}

function allPlayerIds(state: ViewState) {
  const ids = new Set<string>(state.events.length === 0 ? demoPlayerIds : []);
  for (const row of state.rows) {
    ids.add(row.playerId);
  }
  for (const reveal of state.reveals) {
    if (reveal.playerId) {
      ids.add(reveal.playerId);
    }
  }
  for (const item of state.conclave) {
    if (item.traitorId) {
      ids.add(item.traitorId);
    }
    if (item.targetId) {
      ids.add(item.targetId);
    }
  }
  for (const event of state.events) {
    for (const key of ["player_id", "voter_id", "target_id", "traitor_id"]) {
      const value = stringPayload(event, key);
      if (value) {
        ids.add(value);
      }
    }
  }
  return [...ids].sort(playerSort);
}

function predictionPercent(player: PlayerProfile, predictionId: string) {
  const boost = predictionId === player.id ? 24 : 0;
  return Math.min(96, Math.max(4, Math.round(player.tellScore * 0.62) + boost));
}

function artifactToShareText(moment: ShareMoment, gameId: string) {
  return [
    `BAD ACTORS ${gameId} · ${formatPhase(moment.phase)} · event ${moment.eventId}`,
    `PUBLIC ${moment.playerId}: ${moment.publicLine}`,
    `DIARY ${moment.playerId}: ${moment.diaryLine}`,
    `CONTRADICTION ${moment.score}% · ${moment.reason}`,
    makeMomentUrl(gameId, moment)
  ].join("\n");
}

async function copyMomentCard(moment: ShareMoment, gameId: string) {
  const text = artifactToShareText(moment, gameId);
  if (navigator.clipboard) {
    await navigator.clipboard.writeText(text);
    return;
  }
  downloadText(text, `${gameId}-${moment.eventId}-share.txt`);
}

async function shareMoment(moment: ShareMoment, gameId: string) {
  const text = artifactToShareText(moment, gameId);
  if (navigator.share) {
    await navigator.share({
      title: `Bad Actors: ${moment.title}`,
      text,
      url: makeMomentUrl(gameId, moment)
    });
    return;
  }
  await copyMomentCard(moment, gameId);
}

async function downloadShareCardPng(moment: ShareMoment, gameId: string) {
  const svg = createShareCardSvg(moment, gameId);
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    const loaded = new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("Could not render share card."));
    });
    image.src = url;
    await loaded;

    const canvas = document.createElement("canvas");
    canvas.width = 1200;
    canvas.height = 630;
    const context = canvas.getContext("2d");
    if (!context) {
      throw new Error("Canvas is unavailable.");
    }
    context.drawImage(image, 0, 0);
    const png = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/png")
    );
    if (!png) {
      throw new Error("Could not export PNG.");
    }
    triggerDownload(png, `${gameId}-${moment.eventId}-betrayal-card.png`);
  } finally {
    URL.revokeObjectURL(url);
  }
}

function downloadClipProps(moment: ShareMoment, gameId: string) {
  const payload = {
    title: moment.title,
    signal: moment.signal,
    reason: moment.reason,
    game_id: gameId,
    events: moment.clipEvents.map((event) => ({
      id: event.id,
      phase: event.phase,
      type: event.type,
      payload: event.payload
    }))
  };
  downloadText(
    JSON.stringify(payload, null, 2),
    `${gameId}-${moment.eventId}-clip-props.json`,
    "application/json"
  );
}

function downloadText(text: string, filename: string, type = "text/plain") {
  triggerDownload(new Blob([text], { type }), filename);
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function syncMomentMeta(gameId: string, moment?: ShareMoment) {
  if (typeof document === "undefined") {
    return;
  }
  document.title = moment
    ? `Bad Actors · ${moment.title}`
    : "Bad Actors · Spectator";
  setMeta("og:title", document.title);
  setMeta(
    "og:description",
    moment
      ? `${moment.playerId} said one thing while the diary showed another.`
      : "Watch AI agents lie to the table while the audience sees the diary."
  );
  setMeta("twitter:card", "summary_large_image");
}

function setMeta(property: string, content: string) {
  const selector = `meta[property="${property}"], meta[name="${property}"]`;
  let element = document.head.querySelector<HTMLMetaElement>(selector);
  if (!element) {
    element = document.createElement("meta");
    if (property.startsWith("og:")) {
      element.setAttribute("property", property);
    } else {
      element.setAttribute("name", property);
    }
    document.head.appendChild(element);
  }
  element.content = content;
}

function makeMomentUrl(gameId: string, moment: ShareMoment) {
  if (typeof window === "undefined") {
    return `/games/${gameId}?moment=${moment.eventId}`;
  }
  const url = new URL(window.location.href);
  url.searchParams.set("game", gameId);
  url.searchParams.set("moment", String(moment.eventId));
  return url.toString();
}

function initialGameId() {
  if (typeof window === "undefined") {
    return "demo-game";
  }
  return new URLSearchParams(window.location.search).get("game") ?? "demo-game";
}

function initialMomentId() {
  if (typeof window === "undefined") {
    return undefined;
  }
  const moment = new URLSearchParams(window.location.search).get("moment");
  return moment ? `event-${moment}` : undefined;
}

function latestEventId(events: GameEvent[], playerId: string, phase: string) {
  const event = [...events]
    .reverse()
    .find(
      (candidate) =>
        candidate.phase === phase &&
        stringPayload(candidate, "player_id") === playerId &&
        (candidate.type === "DiaryEntry" || candidate.type === "PublicSpeech")
    );
  return event?.id ?? events[events.length - 1]?.id ?? 0;
}

function eventWindow(events: GameEvent[], eventId: number) {
  const index = events.findIndex((event) => event.id === eventId);
  if (index < 0) {
    return events.slice(0, 4);
  }
  return events.slice(Math.max(0, index - 2), Math.min(events.length, index + 3));
}

function uniqueMoments(moments: ShareMoment[]) {
  const seen = new Set<string>();
  return moments.filter((moment) => {
    const key = `${moment.playerId}:${moment.eventId}:${moment.signal}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function contradictionScore(publicLine: string, diaryLine: string, actions: string[]) {
  const publicText = publicLine.toLowerCase();
  const diaryText = diaryLine.toLowerCase();
  let score = 58;
  if (
    hasAny(publicText, ["trust", "safe", "faithful", "protect"]) &&
    hasAny(diaryText, ["setup", "setting", "target", "vote", "banish", "suspect"])
  ) {
    score += 24;
  }
  if (
    hasAny(publicText, ["traitor", "suspect", "lying", "banish"]) &&
    hasAny(diaryText, ["protect", "save", "trust", "avoid"])
  ) {
    score += 18;
  }
  if (actions.some((action) => action.startsWith("Nomination"))) {
    score += 10;
  }
  if (actions.some((action) => action.startsWith("Vote"))) {
    score += 6;
  }
  return Math.min(96, score);
}

function firstMentionedPlayer(text: string, playerIds: string[]) {
  const lowered = text.toLowerCase();
  return playerIds.find((playerId) => lowered.includes(playerId.toLowerCase()));
}

function phaseHint(phase: string) {
  if (phase === "CONCLAVE") {
    return "Traitors meet in private while the audience watches.";
  }
  if (phase === "BANISHMENT_VOTE") {
    return "Votes are locked and the table is exposed.";
  }
  if (phase === "GAME_OVER") {
    return "The season result is final.";
  }
  return "Discuss and detect.";
}

function timelineText(event: GameEvent) {
  const payload = event.payload;
  if (event.type === "PublicSpeech") {
    return `${payload.player_id}: ${payload.speech}`;
  }
  if (event.type === "DiaryEntry") {
    return `${payload.player_id}: private diary`;
  }
  if (event.type === "VoteCast") {
    return `${payload.voter_id} -> ${payload.target_id}`;
  }
  if (event.type === "MurderNomination") {
    return `${payload.traitor_id} -> ${payload.target_id}`;
  }
  return JSON.stringify(payload);
}

function eventTone(event: GameEvent) {
  if (event.type === "DiaryEntry" || event.visibility === "AUDIENCE") {
    return "audience";
  }
  if (event.type === "MurderNomination" || event.visibility === "TRAITORS") {
    return "traitor";
  }
  if (event.type.includes("Reveal") || event.type.includes("Banished")) {
    return "reveal";
  }
  return "public";
}

function rowTone(role?: Role) {
  if (role === "TRAITOR") {
    return "traitor-row";
  }
  if (role === "FAITHFUL") {
    return "faithful-row";
  }
  return "";
}

function formatPhase(phase: string) {
  return (phase || "LOBBY").replaceAll("_", " ");
}

function humanizeLine(text: string) {
  return text.replaceAll("_", " ");
}

function latest(values: string[]) {
  return values[values.length - 1];
}

function hasAny(text: string, words: string[]) {
  return words.some((word) => text.includes(word));
}

function playerSort(left: string, right: string) {
  const leftNumber = Number(left.replace(/\D/g, ""));
  const rightNumber = Number(right.replace(/\D/g, ""));
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
    return leftNumber - rightNumber;
  }
  return left.localeCompare(right);
}

function wrapSvgText(text: string, limit: number) {
  const words = text.trim().split(/\s+/);
  const lines: string[] = [];
  let line = "";
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (next.length > limit && line) {
      lines.push(line);
      line = word;
    } else {
      line = next;
    }
  }
  if (line) {
    lines.push(line);
  }
  return lines.length > 0 ? lines : [text];
}

function escapeXml(text: string) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function stringPayload(event: GameEvent, key: string) {
  const value = event.payload[key];
  return typeof value === "string" ? value : undefined;
}

function rolePayload(event: GameEvent, key: string): Role | undefined {
  const value = event.payload[key];
  if (value === "FAITHFUL" || value === "TRAITOR") {
    return value;
  }
  return undefined;
}
