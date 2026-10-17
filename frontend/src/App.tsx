import { Landing } from "./landing/Landing";
import { useRoute } from "./route";
import { StandingsSite } from "./standings/Site";
import { Viewer } from "./viewer/Viewer";

export function App() {
  const [route, go] = useRoute();
  if (route.page === "viewer") return <Viewer route={route} go={go} />;
  if (route.page !== "landing") return <StandingsSite route={route} />;
  return <Landing />;
}
