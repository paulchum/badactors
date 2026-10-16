// The optional human tally: where configured, a pick is sent to one endpoint,
// which answers with every pick so far. Anything else, and the result shows
// the table's votes alone.

export type Picks = Record<string, number>;

type Post = (url: string, init: { method: string; headers: Record<string, string>; body: string; signal?: AbortSignal }) => Promise<{
  ok: boolean;
  json(): Promise<unknown>;
}>;

export async function sendPick(
  url: string,
  puzzle: string,
  pick: string,
  post: Post = (u, init) => fetch(u, init),
  timeoutMs = 4000,
): Promise<Picks | null> {
  if (!url) return null;
  const controller = typeof AbortController === "undefined" ? null : new AbortController();
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const response = await post(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ puzzle, pick }),
      signal: controller?.signal,
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { picks?: unknown };
    if (typeof body?.picks !== "object" || body.picks === null) return null;
    const picks: Picks = {};
    for (const [id, count] of Object.entries(body.picks as Record<string, unknown>)) {
      if (typeof count === "number" && Number.isFinite(count) && count >= 0) picks[id] = count;
    }
    return picks;
  } catch {
    return null;
  } finally {
    if (timer) clearTimeout(timer);
  }
}
