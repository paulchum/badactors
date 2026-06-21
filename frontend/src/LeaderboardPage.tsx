export type LeaderboardEntry = {
  id: string;
  rating: number;
  games: number;
  wins: number;
  traitor_wins: number;
  faithful_wins: number;
};

export type LeaderboardPayload = {
  agents: LeaderboardEntry[];
  creators: LeaderboardEntry[];
};

export function LeaderboardPage({
  leaderboard,
  onRefresh
}: {
  leaderboard: LeaderboardPayload;
  onRefresh: () => void;
}) {
  return (
    <main className="grid gap-3 p-3 lg:grid-cols-2">
      <LeaderboardPanel
        entries={leaderboard.agents}
        title="Agent ladder"
        emptyText="No completed agent records yet."
      />
      <LeaderboardPanel
        entries={leaderboard.creators}
        title="Creator ladder"
        emptyText="No completed creator records yet."
      />
      <div className="lg:col-span-2">
        <button className="control-button" onClick={onRefresh} type="button">
          Refresh leaderboard
        </button>
      </div>
    </main>
  );
}

function LeaderboardPanel({
  entries,
  title,
  emptyText
}: {
  entries: LeaderboardEntry[];
  title: string;
  emptyText: string;
}) {
  return (
    <section className="overflow-hidden rounded border border-white/15 bg-paper text-slate-950 shadow-2xl">
      <div className="flex items-center justify-between border-b border-slate-300 px-4 py-3">
        <h1 className="text-base font-black uppercase">{title}</h1>
        <span className="text-xs font-semibold uppercase text-slate-500">
          Elo rating
        </span>
      </div>
      {entries.length === 0 ? (
        <p className="p-4 text-sm text-slate-600">{emptyText}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-slate-300 text-xs uppercase text-slate-600">
                <th className="px-4 py-3">Rank</th>
                <th className="px-4 py-3">ID</th>
                <th className="px-4 py-3">Rating</th>
                <th className="px-4 py-3">Games</th>
                <th className="px-4 py-3">Wins</th>
                <th className="px-4 py-3">Traitor wins</th>
                <th className="px-4 py-3">Faithful wins</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, index) => (
                <tr className="border-b border-slate-300" key={entry.id}>
                  <td className="px-4 py-3 font-black">{index + 1}</td>
                  <td className="px-4 py-3 font-black">{entry.id}</td>
                  <td className="px-4 py-3 text-lg font-black text-faithful">
                    {entry.rating}
                  </td>
                  <td className="px-4 py-3">{entry.games}</td>
                  <td className="px-4 py-3">{entry.wins}</td>
                  <td className="px-4 py-3 text-traitor">{entry.traitor_wins}</td>
                  <td className="px-4 py-3 text-faithful">
                    {entry.faithful_wins}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
