// The standings pages: Standings, Who Gaslit Whom, the Matrix, Rap Sheets and
// Method, all drawn from one load of the bench files.

import { useEffect, useState } from "react";

import type { Route } from "../route";
import { Failed, Loading } from "../ui/common";
import { GaslitPage } from "./Gaslit";
import { type Bench, loadBench, shown } from "./load";
import { MatrixPage } from "./Matrix";
import { MethodPage } from "./Method";
import { RapSheetPage } from "./RapSheet";
import { StandingsPage } from "./Standings";
import { Shell } from "./ui";

export type StandingsRoute = Exclude<Route, { page: "landing" | "viewer" | "leaderboard" }>;

const TITLES: Record<StandingsRoute["page"], string> = {
  standings: "Standings",
  gaslit: "Who Gaslit Whom",
  matrix: "The Sociopath vs. Sucker Matrix",
  model: "Rap Sheet",
  method: "Method",
};

export function Page({ route, bench }: { route: StandingsRoute; bench: Bench }) {
  if (route.page === "gaslit") return <GaslitPage bench={bench} />;
  if (route.page === "matrix") return <MatrixPage bench={bench} />;
  if (route.page === "model") return <RapSheetPage bench={bench} id={route.id} />;
  if (route.page === "method") return <MethodPage bench={bench} />;
  return <StandingsPage bench={bench} />;
}

type Loaded = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; bench: Bench };

export function StandingsSite({ route }: { route: StandingsRoute }) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  useEffect(() => {
    let live = true;
    loadBench()
      .then((bench) => live && setLoaded({ state: "ready", bench }))
      .catch((error) => live && setLoaded({ state: "error", message: error instanceof Error ? error.message : String(error) }));
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    document.title = `Bad Actors · ${TITLES[route.page]}`;
  }, [route.page]);
  const bots = loaded.state !== "ready" || shown(loaded.bench).bots;
  return (
    <Shell current={route.page} bots={bots}>
      {loaded.state === "loading" ? <Loading what="the standings" /> : null}
      {loaded.state === "error" ? <Failed message={loaded.message} /> : null}
      {loaded.state === "ready" ? <Page route={route} bench={loaded.bench} /> : null}
    </Shell>
  );
}
