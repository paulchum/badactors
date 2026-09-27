import { useEffect, useRef, useState } from "react";
import { apiFetch, apiJson, ownerToken } from "./api";
import "./world.css";
import { MemoryArchive, WorldSettingsPanel, type LifeMemory } from "./WorldExtras";

type Resident = {
  id: string; name: string; model: string; persona: string; goal: string;
  location: string; energy: number; hunger: number; food: number; coins: number;
  last_action: string; memory_sequence: number;
  objectives: { kind: string; target: number; progress: number; completed_tick: number | null }[];
  stats: { coins_earned: number; community_work: number; arena_wins: number; visited: string[] };
};
type World = {
  id: string; name: string; tick: number; status: "paused" | "running" | "error";
  provider: string; residents: Resident[]; error: string | null; run_until_tick: number;
  places: Record<string, { name: string; description: string; food: number; capacity: number; coins: number }>;
  tick_seconds: number; max_model_requests: number; max_tokens: number; active_arena_id: string | null; progress_since_tick: number;
  projects: Record<string, { name: string; level: number; max_level: number; work: number; work_required: number; contributors: Record<string, number> }>;
  arena_games: string[]; usage?: { reserved_requests: number; reserved_output_tokens: number };
};
type WorldEvent = { sequence: number; tick: number; actor_id: string; type: string; text: string };
type Detail = {
  resident: Resident & { memories: LifeMemory[]; experiences: LifeMemory[]; relationships: Record<string, number> };
  perception: { nearby: { id: string; name: string }[]; valid_actions: string[] };
};
type Template = { residents: { id: string; name: string; model: string; persona: string; goal: string }[]; models: string[] };
const places = [
  { id: "garden", x: 23, y: 23, mark: "❧", color: "#9cd9a5" },
  { id: "workshop", x: 77, y: 23, mark: "⚒", color: "#e7b082" },
  { id: "square", x: 50, y: 48, mark: "◇", color: "#c8c2ec" },
  { id: "hearth", x: 23, y: 77, mark: "♨", color: "#f19c93" },
  { id: "lodge", x: 77, y: 77, mark: "⌂", color: "#a7cbe2" }
];
const colors = ["#c4a7e7", "#a6d5b1", "#efb28f", "#a4c8ed", "#e5b4cc", "#e8d190"];

export function WorldPage({ onArena }: { onArena: (id: string) => void }) {
  const [worlds, setWorlds] = useState<World[]>([]);
  const [worldId, setWorldId] = useState(() => localStorage.getItem("badactors.world.v1") || "");
  const [world, setWorld] = useState<World | null>(null);
  const [events, setEvents] = useState<WorldEvent[]>([]);
  const [residentId, setResidentId] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState(ownerToken);
  const [connection, setConnection] = useState(0);
  const [name, setName] = useState("The Commons");
  const [provider, setProvider] = useState("demo");
  const [model, setModel] = useState("");
  const [template, setTemplate] = useState<Template>({ residents: [], models: [] });
  const [createOpen, setCreateOpen] = useState(false);
  const [diary, setDiary] = useState(false);
  const [runTicks, setRunTicks] = useState(24);
  const generation = useRef(0);

  useEffect(() => { document.title = "Bad Actors · Living Worlds"; }, []);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      apiJson<{ worlds: World[] }>("/worlds", { signal: controller.signal }),
      apiJson<Template>("/worlds/template", { signal: controller.signal })
    ]).then(([list, options]) => {
      setWorlds(list.worlds); setTemplate(options); setError(""); setConnectionError("");
      setModel(options.models[0] || "");
      setWorldId(current => list.worlds.some(item => item.id === current) ? current : list.worlds[0]?.id || "");
    }).catch(caught => { if (!controller.signal.aborted) setConnectionError(String(caught.message)); });
    return () => controller.abort();
  }, [connection]);

  useEffect(() => {
    const current = ++generation.current;
    setEvents([]); setWorld(null); setResidentId(""); setDetail(null);
    if (!worldId) return;
    localStorage.setItem("badactors.world.v1", worldId);
    const controller = new AbortController();
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [snapshot, journal] = await Promise.all([
          apiJson<World>(`/worlds/${worldId}`, { signal: controller.signal }),
          apiJson<{ events: WorldEvent[]; next_cursor: number }>(`/worlds/${worldId}/events?after=${cursor}&limit=500`, { signal: controller.signal })
        ]);
        if (current !== generation.current || controller.signal.aborted) return;
        cursor = journal.next_cursor;
        setWorld(snapshot); setConnectionError("");
        setEvents(previous => [...previous, ...journal.events].slice(-120));
        setResidentId(previous => previous || snapshot.residents[0]?.id || "");
        timer = setTimeout(refresh, journal.events.length === 500 ? 50 : 1500);
      } catch (caught) {
        if (controller.signal.aborted) return;
        setConnectionError(caught instanceof Error ? caught.message : "World connection failed.");
        timer = setTimeout(refresh, 4000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [worldId, connection]);

  useEffect(() => {
    setDetail(null);
    if (!worldId || !residentId) return;
    const controller = new AbortController();
    apiJson<Detail>(`/worlds/${worldId}/residents/${residentId}`, { signal: controller.signal })
      .then(setDetail).catch(caught => { if (!controller.signal.aborted) setConnectionError(caught.message); });
    return () => controller.abort();
  }, [worldId, residentId, world?.tick, connection, world?.arena_games.length]);

  async function action(kind: string) {
    if (!world || busy) return;
    setBusy(true); setError("");
    try {
      if (kind === "arena") {
        const result = await apiJson<{ game_id: string }>(`/worlds/${world.id}/arena`, { method: "POST" });
        onArena(result.game_id);
      } else if (kind === "cancel-arena") {
        setWorld(await apiJson<World>(`/worlds/${world.id}/arena/cancel`, { method: "POST" }));
      } else {
        setWorld(await apiJson<World>(`/worlds/${world.id}/control`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: kind, ticks: runTicks })
        }));
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Action failed."); }
    finally { setBusy(false); }
  }

  async function create() {
    setBusy(true); setError("");
    try {
      const created = await apiJson<World>("/worlds", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: `commons-${Date.now()}`, name, provider,
          ...(provider === "openrouter" ? { residents: template.residents.map(resident => ({ ...resident, model })) } : {}) })
      });
      setWorlds(previous => [...previous, created]); setWorldId(created.id); setCreateOpen(false);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Creation failed."); }
    finally { setBusy(false); }
  }

  async function download() {
    if (!world) return;
    try {
      const response = await apiFetch(`/worlds/${world.id}/export`);
      if (!response.ok) throw new Error("Export failed.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `${world.id}.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Export failed."); }
  }

  const project = world?.projects["garden-irrigation"];
  const selected = world?.residents.find(resident => resident.id === residentId);
  return <main className="world-page">
    <header className="world-heading">
      <div><p className="world-eyebrow">BAD ACTORS / LIVING WORLDS</p><h1>A life beyond the game.</h1>
        <p>They make plans, cross paths, and carry the past with them.</p></div>
      <div className="world-select"><label htmlFor="world-select">Your worlds</label>
        <select id="world-select" value={worldId} onChange={event => setWorldId(event.target.value)} disabled={busy}>
          <option value="" disabled>Choose a world</option>{worlds.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select><button onClick={() => setCreateOpen(!createOpen)}>+ New world</button></div>
    </header>
    <details className="world-access"><summary>Owner access</summary><form onSubmit={event => {
      event.preventDefault(); sessionStorage.setItem("badactors.owner.v1", token); setConnection(value => value + 1);
    }}><label>Server owner token <input type="password" value={token} onChange={event => setToken(event.target.value)} autoComplete="off" /></label>
      <button type="submit">Connect</button><small>Only needed when your server requires a token. Kept in this browser tab.</small></form></details>
    {error || connectionError ? <div role="alert" className="world-error">{error || connectionError}</div> : null}
    {createOpen || (!worldId && !error && !connectionError) ? <form className="world-create" onSubmit={event => { event.preventDefault(); void create(); }}>
      <div><h2>Start a small society.</h2><p>Six distinct residents. Five places. An open-ended future.</p></div>
      <label>World name<input value={name} onChange={event => setName(event.target.value)} maxLength={80} required /></label>
      <label>Resident decisions<select value={provider} onChange={event => setProvider(event.target.value)}>
        <option value="demo">Demo policy · no model calls</option><option value="openrouter">Live AI · OpenRouter</option></select></label>
      {provider === "openrouter" ? <label>Approved model<select value={model} onChange={event => setModel(event.target.value)} required>
        <option value="" disabled>Configure models on your server</option>{template.models.map(item => <option key={item}>{item}</option>)}
      </select><small>1,000 request reservations per world. 400 output tokens per attempt.</small></label> : null}
      <button className="world-primary" disabled={busy} type="submit">Create world →</button>
    </form> : null}
    {world ? <>
      <section className="world-toolbar" aria-label="World controls">
        <div><span className={`world-status ${world.status}`}>{world.status}</span><strong>{world.name}</strong>
          <span>Day {Math.floor(world.tick / 24) + 1} · {['Morning', 'Afternoon', 'Evening', 'Night'][Math.floor(world.tick % 24 / 6)]} · Tick {world.tick}</span></div>
        <div><span className="world-provider">{world.provider === "demo" ? "DEMO POLICY · NO LLM" : "LIVE MODEL DECISIONS"}</span>
          <label>Run length<select value={runTicks} onChange={event => setRunTicks(Number(event.target.value))} disabled={busy || world.status === "running"}>
            <option value={24}>24 ticks · one day</option><option value={120}>120 ticks · five days</option><option value={1000}>1,000 ticks</option></select></label>
          <button disabled={busy || Boolean(world.active_arena_id)} className="world-primary" onClick={() => void action(world.status === "running" ? "pause" : "run")}>{world.status === "running" ? "Pause world" : `Run ${runTicks} ticks`}</button>
          <button disabled={busy || world.status === "running" || Boolean(world.active_arena_id)} onClick={() => void action("step")}>Step once</button></div>
      </section>
      <WorldSettingsPanel key={world.id} world={world} onSaved={() => setConnection(value => value + 1)} />
      {world.active_arena_id ? <div className="world-arena-notice" role="status">The residents are in challenge {world.active_arena_id}. Resume it or cancel below before village life continues.</div> : null}
      {world.error ? <div className="world-error" role="alert">{world.error}</div> : null}
      <div className="world-layout">
        <section className="world-map-panel" aria-label="World map">
          <div className="world-panel-label"><span>THE COMMONS</span><span>{world.residents.length} residents · {world.status === "running" ? `${world.run_until_tick - world.tick} ticks left` : "Saved to disk"}</span></div>
          <div className="world-map">
            <svg className="world-paths" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              <path d="M23 23 L50 48 L77 23 M23 77 L50 48 L77 77 M23 23 L23 77 M77 23 L77 77" />
            </svg>
            <div className="world-map-compass" aria-hidden="true">N<br />↑</div>
            {places.map(place => <div key={place.id} className="world-place" style={{ left: `${place.x}%`, top: `${place.y}%` }}>
              <span className="world-place-mark" style={{ color: place.color }}>{place.mark}</span><h3>{world.places[place.id]?.name}</h3>
              <small>{place.id === "garden" ? `${world.places.garden.food} food growing` : world.places[place.id]?.description}</small>
              <div className="world-occupants">{world.residents.filter(resident => resident.location === place.id).map(resident => <button
                key={resident.id} title={`${resident.name}: ${resident.last_action}`} aria-label={`Inspect ${resident.name}`} aria-pressed={residentId === resident.id}
                onClick={() => setResidentId(resident.id)} style={{ background: colors[world.residents.indexOf(resident) % colors.length] }}>
                {resident.name.slice(0, 1)}</button>)}</div>
            </div>)}
          </div>
          <div className="world-community">
            <div><span>THE HEARTH MARKET</span><p><b>{world.places.hearth.food}</b> food in stock · <b>{world.places.hearth.coins}</b> coins</p><small>Buy food for 2 coins. Sell for 1.</small></div>
            {project ? <div><span>{project.name.toUpperCase()}</span><p>Level <b>{project.level}</b> / {project.max_level} · {Object.keys(project.contributors).length} contributors</p>
              <progress aria-label="Irrigation level progress" value={project.level === project.max_level ? project.work_required : project.work} max={project.work_required} />
              <small>{project.level === project.max_level ? "Fully built" : `${project.work} / ${project.work_required} contributions to next level`} · garden holds {world.places.garden.capacity} food</small></div> : null}
          </div>
          <footer className="world-map-footer"><span>Every path leads to a consequence.</span><button onClick={() => void download()}>Export world ↗</button></footer>
        </section>
        <aside className="world-resident-panel">
          <div className="world-panel-label"><span>RESIDENT PROFILE</span><span>{selected?.location}</span></div>
          {selected ? <>
            <div className="world-resident-title"><span className="world-avatar" style={{ background: colors[world.residents.indexOf(selected) % colors.length] }}>{selected.name[0]}</span>
              <div><h2>{selected.name}</h2><span>{selected.model === "demo" ? "Demo resident" : selected.model}</span></div></div>
            <p className="world-persona">{selected.persona}</p><div className="world-goal"><span>WORKING TOWARD</span><p>{selected.goal}</p></div>
            {selected.objectives?.length ? <div className="world-objectives">{selected.objectives.map((goal, index) => <div key={`${goal.kind}-${index}`}>
              <span>{goal.kind.replaceAll('_', ' ')} <b>{goal.completed_tick !== null ? `✓ T${goal.completed_tick}` : `${goal.progress} / ${goal.target}`}</b></span>
              <progress aria-label={`${goal.kind.replaceAll('_', ' ')} progress`} max={goal.target} value={Math.min(goal.target, goal.progress)} /></div>)}</div> : null}
            <div className="world-needs"><label>Energy <b>{selected.energy}</b><meter min={0} max={100} value={selected.energy} /></label>
              <label>Hunger <b>{selected.hunger}</b><meter min={0} max={100} value={selected.hunger} /></label></div>
            <div className="world-inventory"><span><b>{selected.food}</b> food</span><span><b>{selected.coins}</b> coins</span><span><b>{Object.keys(detail?.resident.relationships || {}).length}</b> connections</span></div>
            <p className="world-fineprint">{world.progress_since_tick ? `Recorded since T${world.progress_since_tick}` : 'Lifetime'}: {selected.stats?.coins_earned || 0} coins earned · {selected.stats?.visited.length || 0} places visited · {selected.stats?.arena_wins || 0} arena wins</p>
            {detail?.resident.experiences.length ? <details className="world-experiences"><summary>Lasting milestones</summary>{[...detail.resident.experiences].reverse().map(memory => <p key={memory.sequence}>T{memory.tick} · {memory.text}</p>)}</details> : null}
            <p className="world-last-action">{selected.last_action}</p>
            <div className="world-memory-tabs"><button aria-pressed={!diary} onClick={() => setDiary(false)}>What they observed</button><button aria-pressed={diary} onClick={() => setDiary(true)}>Private diary</button></div>
            <div className="world-memories">{detail?.resident.memories.filter(memory => diary ? memory.kind === "reflection" : memory.kind !== "reflection").slice(-6).reverse().map((memory, index) =>
              <p key={`${memory.tick}-${index}`}><span>T{memory.tick}</span>{memory.text}</p>)}
              {!detail?.resident.memories.length ? <p>A new arrival. Their story starts with the first tick.</p> : null}</div>
            <p className="world-fineprint">{diary ? "A generated character diary, visible to the audience. Other residents cannot read it." : `Nearby: ${detail?.perception.nearby.map(item => item.name).join(', ') || 'no one'}. Memories come from their own experience.`}</p>
            <MemoryArchive key={`${world.id}:${selected.id}`} worldId={world.id} residentId={selected.id} available={selected.memory_sequence} />
          </> : null}
        </aside>
        <section className="world-journal"><div className="world-panel-label"><span>THE STORY SO FAR</span><span>Latest {events.length} events</span></div>
          <div className="world-journal-rows">{[...events].reverse().slice(0, 18).map(event => <article key={event.sequence}><span>T{event.tick.toString().padStart(3, "0")}</span><p>{event.text}</p><small>{event.type}</small></article>)}
            {!events.length ? <p className="world-empty">The village is quiet. Advance time to begin.</p> : null}</div></section>
        <section className="world-arena"><p className="world-eyebrow">A TEST OF CHARACTER</p><h2>Old friends.<br />New secrets.</h2>
          <p>Send the residents to the deduction table. They take their own memories with them. Winners return with five coins.</p>
          <button disabled={busy || world.status === "running"} onClick={() => void action("arena")}>{busy ? "Working…" : world.active_arena_id ? "Resume the arena →" : "Enter the arena →"}</button>
          {world.active_arena_id ? <button disabled={busy} onClick={() => void action("cancel-arena")}>Cancel unfinished challenge</button> : null}
          <small>{world.status === "running" ? "Pause the world to begin a challenge." : `${world.arena_games.length} challenges completed`}</small>
          {world.provider !== "demo" ? <small>{world.usage?.reserved_requests || 0} model requests reserved. Arena calls share this world's allowance.</small> : null}
        </section>
      </div>
    </> : !createOpen && worldId ? <p className="world-empty">Loading your world…</p> : null}
  </main>;
}
