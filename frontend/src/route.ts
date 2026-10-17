// The site's pages live in the query string, so the static build needs no
// server routes: "?game=seed-56&tick=41" is the viewer, "?game=seed-56&claim=
// c927-1" a claim's receipt in it, the bare page the landing game.

import { useCallback, useEffect, useState } from "react";

export type Route =
  | { page: "landing" }
  | { page: "viewer"; game: string | null; tick: number | null; moment: string | null; claim?: string | null }
  | { page: "leaderboard" }
  | { page: StandingsPage }
  | { page: "model"; id: string };

export type StandingsPage = "standings" | "gaslit" | "matrix" | "method";
const STANDINGS: readonly string[] = ["standings", "gaslit", "matrix", "method"];

export function parseRoute(search: string): Route {
  const params = new URLSearchParams(search);
  if (params.get("page") === "leaderboard") return { page: "leaderboard" };
  const page = params.get("page") ?? "";
  if (STANDINGS.includes(page)) return { page: page as StandingsPage };
  if (page === "model" && params.get("id")) return { page: "model", id: params.get("id") ?? "" };
  if (params.has("game") || params.get("page") === "games") {
    const tick = Number.parseInt(params.get("tick") ?? "", 10);
    return {
      page: "viewer",
      game: params.get("game") || null,
      tick: Number.isFinite(tick) && tick >= 0 ? tick : null,
      moment: params.get("moment") || null,
      claim: params.get("claim") || null,
    };
  }
  return { page: "landing" };
}

export function routeSearch(route: Route): string {
  if (route.page === "landing") return "";
  if (route.page === "leaderboard") return "?page=leaderboard";
  if (route.page === "model") return `?${new URLSearchParams({ page: "model", id: route.id }).toString()}`;
  if (route.page !== "viewer") return `?page=${route.page}`;
  const params = new URLSearchParams();
  params.set("game", route.game ?? "");
  if (route.tick !== null) params.set("tick", String(route.tick));
  if (route.moment !== null) params.set("moment", route.moment);
  if (route.claim) params.set("claim", route.claim);
  return `?${params.toString()}`;
}

/** A link to a page, relative to this one, so it works under any base path. */
export function href(route: Route): string {
  return routeSearch(route) || "./";
}

function currentPath(): string {
  return typeof window === "undefined" ? "/" : window.location.pathname;
}

export function useRoute(): [Route, (next: Route, how?: "push" | "replace") => void] {
  const [route, setRoute] = useState<Route>(() =>
    typeof window === "undefined" ? { page: "landing" } : parseRoute(window.location.search),
  );
  useEffect(() => {
    const onPop = () => setRoute(parseRoute(window.location.search));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const go = useCallback((next: Route, how: "push" | "replace" = "push") => {
    const url = `${currentPath()}${routeSearch(next)}`;
    if (how === "push") window.history.pushState(null, "", url);
    else window.history.replaceState(null, "", url);
    setRoute(next);
    if (how === "push") window.scrollTo({ top: 0 });
  }, []);
  return [route, go];
}
