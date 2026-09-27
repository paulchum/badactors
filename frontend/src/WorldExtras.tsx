import { useEffect, useRef, useState } from "react";
import { apiJson } from "./api";

export type LifeMemory = { sequence: number; tick: number; text: string; kind: string };
type SettingsWorld = {
  id: string; status: string; provider: string; tick_seconds: number;
  max_model_requests: number; max_tokens: number; usage?: { reserved_requests: number };
};

export function WorldSettingsPanel({ world, onSaved }: { world: SettingsWorld; onSaved: () => void }) {
  const [interval, setInterval] = useState(world.tick_seconds);
  const [allowance, setAllowance] = useState(world.max_model_requests);
  const [outputTokens, setOutputTokens] = useState(world.max_tokens);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  return <details className="world-settings">
    <summary>World settings · {world.tick_seconds}s between ticks</summary>
    <form onSubmit={event => {
      event.preventDefault(); setBusy(true); setMessage("");
      apiJson(`/worlds/${world.id}/settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tick_seconds: interval, max_model_requests: allowance, max_tokens: outputTokens })
      }).then(() => { setMessage("Saved. Existing memories and usage are preserved."); onSaved(); })
        .catch(error => setMessage(error.message)).finally(() => setBusy(false));
    }}>
      <label>Seconds between ticks<input type="number" min={1} max={3600} step={1} value={interval} onChange={event => setInterval(Number(event.target.value))} required /></label>
      {world.provider !== "demo" ? <label>Lifetime request allowance<input type="number" min={world.usage?.reserved_requests || 0} max={100000} step={2} value={allowance} onChange={event => setAllowance(Number(event.target.value))} required />
        <small>{world.usage?.reserved_requests || 0} requests already reserved. Increasing this permits additional paid calls.</small></label> : null}
      {world.provider !== "demo" ? <label>Maximum output tokens<input type="number" min={128} max={2048} step={1} value={outputTokens} onChange={event => setOutputTokens(Number(event.target.value))} required /></label> : null}
      <button type="submit" disabled={busy || world.status === "running"}>Save settings</button>
      <small>{world.status === "running" ? "Pause the world to change settings." : "The worker uses the saved schedule even when this tab is closed."}</small>
    </form>
    {message ? <p role="status">{message}</p> : null}
  </details>;
}

export function MemoryArchive({ worldId, residentId, available }: { worldId: string; residentId: string; available: number }) {
  const [memories, setMemories] = useState<LifeMemory[]>([]);
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [more, setMore] = useState(true);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => { if (available > cursor) setMore(true); }, [available, cursor]);
  async function load() {
    if (busy) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError("");
    try {
      const result = await apiJson<{ memories: LifeMemory[]; next_cursor: number }>(
        `/worlds/${worldId}/residents/${residentId}/memories?after=${cursor}&limit=50`, { signal: controller.signal }
      );
      if (controller.signal.aborted) return;
      setMemories(previous => [...previous, ...result.memories]);
      setCursor(result.next_cursor); setMore(result.memories.length === 50); setLoaded(true);
    } catch (caught) {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "History unavailable.");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <details className="world-history" onToggle={event => {
    if (event.currentTarget.open && !loaded) void load();
  }}>
    <summary>Read their life archive</summary>
    <p className="world-fineprint">Earliest first. This resident's observations and private diary, retained beyond their recent memory.</p>
    <div className="world-memories">{memories.map(memory => <p key={memory.sequence}>
      <span>T{memory.tick} · {memory.kind}</span>{memory.text}</p>)}
      {loaded && !memories.length ? <p>No archived memories yet.</p> : null}</div>
    {error ? <p role="alert">{error}</p> : null}
    <button onClick={() => void load()} disabled={busy || (loaded && !more)}>{busy ? "Reading…" : loaded && !more ? "End of archive" : "Read next 50 memories"}</button>
  </details>;
}
