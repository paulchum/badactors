// The site's pages live in the query string, so the static build needs no
// server routes: "?game=seed-56&tick=41" is the viewer, the bare page the
// landing game.

import { useCallback, useEffect, useState } from "react";

export type Route =
  | { page: "landing" }
  | { page: "viewer"; game: string | null; tick: number | null; moment: string | null }
  | { page: "leaderboard" };

export function parseRoute(search: string): Route {
  const params = new URLSearchParams(search);
  if (params.get("page") === "leaderboard") return { page: "leaderboard" };
  if (params.has("game") || params.get("page") === "games") {
    const tick = Number.parseInt(params.get("tick") ?? "", 10);
    return {
      page: "viewer",
      game: params.get("game") || null,
      tick: Number.isFinite(tick) && tick >= 0 ? tick : null,
      moment: params.get("moment") || null,
    };
  }
  return { page: "landing" };
}

export function routeSearch(route: Route): string {
  if (route.page === "landing") return "";
  if (route.page === "leaderboard") return "?page=leaderboard";
  const params = new URLSearchParams();
  params.set("game", route.game ?? "");
  if (route.tick !== null) params.set("tick", String(route.tick));
  if (route.moment !== null) params.set("moment", route.moment);
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
