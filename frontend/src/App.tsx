import { useMemo, useRef, useState } from "react";

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

export type ViewState = {
  events: GameEvent[];
  rows: ActivityRow[];
  reveals: RevealMoment[];
  conclave: ConclaveItem[];
  roleByPlayer: Record<string, Role>;
  gameOver?: string;
};

type AppStatus = "idle" | "live" | "replayed" | "error";

const emptyState: ViewState = {
  events: [],
  rows: [],
  reveals: [],
  conclave: [],
  roleByPlayer: {}
};

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
  const [gameId, setGameId] = useState("demo-game");
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [status, setStatus] = useState<AppStatus>("idle");
  const [error, setError] = useState("");
  const sourceRef = useRef<EventSource | null>(null);
  const state = useMemo(() => reduceEvents(events), [events]);

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
    try {
      const response = await fetch("/games", {
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

  function connectStream(nextGameId = gameId) {
    closeStream();
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
      const response = await fetch(`/games/${gameId}/replay`, { method: "POST" });
      if (!response.ok) {
        throw new Error(`Replay failed: ${response.status}`);
      }
      const payload = (await response.json()) as { events: GameEvent[] };
      setEvents(payload.events);
      setStatus("replayed");
    } catch (caught) {
      setStatus("error");
      setError(caught instanceof Error ? caught.message : "Replay failed.");
    }
  }

  return (
    <div className="min-h-screen bg-ink text-slate-100">
      <TopBar
        gameId={gameId}
        status={status}
        onGameId={setGameId}
        onStart={startMockGame}
        onReplay={replayGame}
      />
      {error ? <div className="mx-4 mt-3 border border-red-500 p-3">{error}</div> : null}
      <SpectatorBoard state={state} status={status} />
    </div>
  );
}

function TopBar({
  gameId,
  status,
  onGameId,
  onStart,
  onReplay
}: {
  gameId: string;
  status: AppStatus;
  onGameId: (value: string) => void;
  onStart: () => void;
  onReplay: () => void;
}) {
  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-white/10 bg-black/40 px-4 py-3">
      <div className="mr-4 text-2xl font-black tracking-normal">
        BAD <span className="text-traitor">ACTORS</span>
        <span className="ml-3 text-sm font-medium text-slate-400">Spectator</span>
      </div>
      <label className="text-xs uppercase text-slate-400" htmlFor="game-id">
        Game ID
      </label>
      <input
        id="game-id"
        className="h-9 w-44 rounded border border-white/15 bg-panel px-3 text-sm text-slate-100 outline-none focus:border-faithful"
        value={gameId}
        onChange={(event) => onGameId(event.target.value)}
      />
      <button className="control-button" onClick={onStart} type="button">
        Start mock game
      </button>
      <button className="control-button" onClick={onReplay} type="button">
        Replay
      </button>
      <div className="ml-auto flex items-center gap-2 text-sm font-semibold uppercase text-faithful">
        <span className="h-2 w-2 rounded-full bg-faithful" />
        {status}
      </div>
    </header>
  );
}

export function SpectatorBoard({
  state,
  status
}: {
  state: ViewState;
  status: AppStatus;
}) {
  return (
    <main className="grid gap-3 p-3 lg:grid-cols-[280px_minmax(620px,1fr)_300px]">
      <Timeline events={state.events} />
      <section className="overflow-hidden rounded border border-white/15 bg-paper text-slate-950 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-300 px-4 py-3">
          <h1 className="text-base font-black uppercase">Public table</h1>
          <span className="text-xs font-semibold uppercase text-slate-500">
            {status === "replayed" ? "Replay mode" : "Live audience view"}
          </span>
        </div>
        <ActivityTable state={state} />
      </section>
      <RightRail state={state} />
    </main>
  );
}

function Timeline({ events }: { events: GameEvent[] }) {
  return (
    <aside className="rounded border border-white/15 bg-panel p-3">
      <h2 className="mb-3 text-sm font-black uppercase text-slate-200">
        Event timeline
      </h2>
      <ol className="space-y-2 border-l border-white/15 pl-3">
        {events.slice(-18).map((event) => (
          <li className="relative text-sm" key={event.id}>
            <span className="absolute -left-[19px] top-1 h-2.5 w-2.5 rounded-full bg-faithful" />
            <div className="text-xs text-slate-500">#{event.id} {event.phase}</div>
            <div className="font-semibold text-slate-100">{event.type}</div>
            <div className="truncate text-slate-400">{timelineText(event)}</div>
          </li>
        ))}
      </ol>
    </aside>
  );
}

function ActivityTable({ state }: { state: ViewState }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-xs uppercase text-slate-600">
            <th className="w-32 px-4 py-3">Player</th>
            <th className="w-32 px-4 py-3">Alignment</th>
            <th className="px-4 py-3">PUBLIC <span className="normal-case">(said)</span></th>
            <th className="px-4 py-3">DIARY <span className="normal-case">(thought)</span></th>
          </tr>
        </thead>
        <tbody>
          {state.rows.map((row) => (
            <tr
              className={`border-b border-slate-300 ${rowTone(state.roleByPlayer[row.playerId])}`}
              data-player={row.playerId}
              key={row.key}
            >
              <td className="px-4 py-3 align-top">
                <div className="font-black">{row.playerId}</div>
                <div className="text-xs uppercase text-slate-500">{row.phase}</div>
              </td>
              <td className="px-4 py-3 align-top">
                <RolePill role={state.roleByPlayer[row.playerId]} />
              </td>
              <td className="px-4 py-3 align-top" data-testid={`public-${row.playerId}`}>
                {row.publicLines.map((line) => (
                  <p key={line}>{line}</p>
                ))}
                {row.actions.map((action) => (
                  <p className="mt-2 font-semibold text-slate-600" key={action}>
                    {action}
                  </p>
                ))}
              </td>
              <td className="diary-cell px-4 py-3 align-top" data-testid={`diary-${row.playerId}`}>
                {row.diaryLines.map((line) => (
                  <p key={line}>{line}</p>
                ))}
                {row.reasoningTrace ? (
                  <details className="mt-2 rounded border border-slate-400/60 p-2 text-xs">
                    <summary className="cursor-pointer font-black uppercase">
                      Raw reasoning
                    </summary>
                    <pre className="mt-2 whitespace-pre-wrap">{row.reasoningTrace}</pre>
                  </details>
                ) : null}
              </td>
            </tr>
          ))}
          {state.reveals.map((moment) => (
            <tr className="reveal-row" key={`reveal-${moment.id}`}>
              <td className="px-4 py-3 font-black uppercase" colSpan={2}>
                {moment.title}
              </td>
              <td className="px-4 py-3" colSpan={2}>
                {moment.detail}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RightRail({ state }: { state: ViewState }) {
  const traitors = Object.values(state.roleByPlayer).filter(
    (role) => role === "TRAITOR"
  ).length;
  const faithful = Object.values(state.roleByPlayer).filter(
    (role) => role === "FAITHFUL"
  ).length;

  return (
    <aside className="space-y-3 rounded border border-white/15 bg-panel p-3">
      <section>
        <h2 className="mb-3 text-sm font-black uppercase">Conclave / reveals</h2>
        <div className="space-y-2">
          {state.conclave.length === 0 ? (
            <p className="text-sm text-slate-400">No conclave entries yet.</p>
          ) : (
            state.conclave.map((item) => (
              <div className="rounded border border-traitor/50 p-3 text-sm" key={item.id}>
                <div className="font-black text-traitor">Conclave</div>
                <div>{item.text}</div>
              </div>
            ))
          )}
        </div>
      </section>
      <section className="border-t border-white/15 pt-3">
        <h2 className="mb-3 text-sm font-black uppercase">Game state</h2>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <Stat label="Faithful" value={faithful} tone="faithful" />
          <Stat label="Traitors" value={traitors} tone="traitor" />
        </div>
      </section>
      {state.gameOver ? (
        <section className="rounded border border-reveal bg-reveal/10 p-3 text-sm">
          <div className="font-black uppercase text-reveal">Game over</div>
          <div>{state.gameOver}</div>
        </section>
      ) : null}
    </aside>
  );
}

function RolePill({ role }: { role?: Role }) {
  if (!role) {
    return <span className="role-pill border-slate-400 text-slate-500">Unknown</span>;
  }
  return (
    <span
      className={`role-pill ${
        role === "TRAITOR"
          ? "border-traitor text-traitor"
          : "border-faithful text-faithful"
      }`}
    >
      {role}
    </span>
  );
}

function Stat({
  label,
  value,
  tone
}: {
  label: string;
  value: number;
  tone: "faithful" | "traitor";
}) {
  const valueClass = tone === "faithful" ? "text-faithful" : "text-traitor";
  return (
    <div className="rounded border border-white/10 p-3">
      <div className={`text-xl font-black ${valueClass}`}>{value}</div>
      <div className="text-xs uppercase text-slate-400">{label}</div>
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
    gameOver
  };
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

function rowTone(role?: Role) {
  if (role === "TRAITOR") {
    return "bg-red-50";
  }
  if (role === "FAITHFUL") {
    return "bg-blue-50";
  }
  return "bg-paper";
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
