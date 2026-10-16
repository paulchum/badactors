// Site settings, fixed at build time from VITE_* environment variables. All are
// optional: with none set the site is fully static and asks no server anything.

const env = (import.meta.env ?? {}) as Record<string, string | undefined>;

/** Where the landing game sends a player's pick, if anywhere. The endpoint
 * takes a POST of JSON {"puzzle": "<game>:<puzzle>", "pick": "<seat id>"} and
 * answers JSON {"picks": {"<seat id>": <count>, ...}} with every pick so far.
 * With none set, the result shows only the players' own votes at the table. */
export const TALLY_URL = env.VITE_TALLY_URL?.trim() || "";

export const REPO_URL = env.VITE_REPO_URL?.trim() || "https://github.com/paulchum/badactors";

/** The leaderboard, once real-model games are rated. Until then the site's own
 * page says there is none yet. */
export const LEADERBOARD_URL = env.VITE_LEADERBOARD_URL?.trim() || "";
