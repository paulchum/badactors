const COMMANDS = [
  {
    command: "uv run castle-game --seed 7",
    note: "One whole game with the seeded bot in every seat, told from the audience's side."
  },
  {
    command: "uv run castle-sweep",
    note: "Two hundred games, and how each tool and manipulation fared in them."
  },
  {
    command: "uv run python scripts/castle_three_days.py",
    note: "Three days of the world itself, with the rolls behind every sighting."
  }
];

export function App() {
  return (
    <div className="app-shell">
      <header className="flex items-center border-b border-white/10 px-4 py-4 sm:px-6">
        <div className="brand-lockup" aria-label="Bad Actors">
          <span>BAD</span>
          <span className="brand-mask">ACTORS</span>
        </div>
      </header>
      <main className="mx-auto grid max-w-3xl gap-6 px-4 py-10 sm:px-6">
        <section className="grid gap-3">
          <h1 className="text-3xl font-black tracking-tight">The Castle</h1>
          <p className="text-slate-300">
            Frontier models play a hidden-role game in an eleven-room castle. The audience
            sees what each player says, what it privately thinks, and what it actually did.
          </p>
          <p className="text-slate-400">
            The spectator for the Castle is being built. Until then, games run from the
            command line:
          </p>
        </section>
        <ul className="grid gap-3" aria-label="Castle commands">
          {COMMANDS.map(({ command, note }) => (
            <li key={command} className="rounded-lg border border-white/10 bg-panel p-4">
              <code className="block break-words font-mono text-reveal">{command}</code>
              <p className="mt-2 text-sm text-slate-400">{note}</p>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
