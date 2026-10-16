import { Landing } from "./landing/Landing";
import { Leaderboard } from "./landing/Leaderboard";
import { useRoute } from "./route";
import { Viewer } from "./viewer/Viewer";

export function App() {
  const [route, go] = useRoute();
  if (route.page === "viewer") return <Viewer route={route} go={go} />;
  if (route.page === "leaderboard") return <Leaderboard />;
  return <Landing />;
}
