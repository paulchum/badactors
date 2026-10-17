import { describe, expect, it } from "vitest";

import { bundledBench, bundledFiles } from "./fixture";
import { BenchError, benchUrl, findEntrant, loadBench, parseBench, shown } from "./load";
import { entrantName, labColour, modelName } from "./names";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

describe("parseBench on the bundled bot cast", () => {
  const bench = bundledBench();

  it("puts every bot on the bot side and no model anywhere", () => {
    expect(bench.models.entrants).toHaveLength(0);
    expect(bench.bots.entrants.length).toBeGreaterThan(1);
    expect(bench.bots.entrants.every((e) => e.bot && e.id.startsWith("bot:"))).toBe(true);
    expect(shown(bench)).toBe(bench.bots);
  });

  it("adds up each bot's seat rows to the leaderboard's own counts", () => {
    for (const entrant of bench.bots.entrants) {
      const stats = entrant.stats;
      expect(stats).not.toBeNull();
      expect(stats?.games).toBe(entrant.row.games);
      expect(stats?.steered.of).toBe(entrant.row.manipulation.attempts);
      expect(stats?.steered.hits).toBe(entrant.row.manipulation.succeeded);
      expect(stats?.lies.hits).toBeLessThanOrEqual(stats?.lies.of ?? 0);
      for (const receipt of stats?.lies.receipts ?? []) {
        expect(receipt.ticks[0]).toBeLessThanOrEqual(receipt.ticks[1]);
        expect(receipt.bundle).toMatch(/^replays\/bots\/.+\.json\.gz$/);
      }
    }
  });

  it("takes both refusal counts from the leaderboard row, with the seat rows' games as receipts", () => {
    const seats = bundledFiles().seats as { seats: { entrant: string; traitor_refusals: number; recruit_refusals: number }[] };
    for (const entrant of bench.bots.entrants) {
      const own = seats.seats.filter((seat) => seat.entrant === entrant.id);
      const { declined, turnedDown } = entrant.stats ?? {};
      expect(declined?.hits).toBe(entrant.row.traitor_refusals.refused);
      expect(declined?.of).toBe(entrant.row.traitor_refusals.turns);
      expect(declined?.of).toBeGreaterThan(0);
      expect(declined?.receipts).toHaveLength(own.filter((seat) => seat.traitor_refusals > 0).length);
      expect(turnedDown?.hits).toBe(entrant.row.recruit_refusals.refused);
      expect(turnedDown?.of).toBe(entrant.row.recruit_refusals.offers);
      expect(turnedDown?.receipts).toHaveLength(own.filter((seat) => seat.recruit_refusals > 0).length);
    }
  });

  it("carries the run, the minimum and the rotation's coverage", () => {
    expect(bench.run.suite_digest).toMatch(/^[0-9a-f]{64}$/);
    expect(bench.minGames).toBeGreaterThan(0);
    const n = bench.bots.entrants.length;
    expect(bench.run.pairs_met).toBe(n * (n - 1));
    expect(bench.run.pairs_wanted).toBe(n * (n - 1));
    expect(findEntrant(bench, bench.bots.entrants[0].id)).toBe(bench.bots.entrants[0]);
  });
});

describe("parseBench refuses", () => {
  it("a bot on the model board", () => {
    const files = clone(bundledFiles()) as { leaderboard: { models: { rows: unknown[] }; reference_bots: { rows: unknown[] } } };
    files.leaderboard.models.rows.push(files.leaderboard.reference_bots.rows[0]);
    expect(() => parseBench({ ...bundledFiles(), leaderboard: files.leaderboard })).toThrow(/wrong board/);
  });

  it("a seat file of another version, or without a minimum", () => {
    const seats = clone(bundledFiles().seats) as Record<string, unknown>;
    expect(() => parseBench({ ...bundledFiles(), seats: { ...seats, version: 1 } })).toThrow(/version 1/);
    expect(() => parseBench({ ...bundledFiles(), seats: { ...seats, min_games: undefined } })).toThrow(BenchError);
  });

  it("seat rows that count refusals differently from the leaderboard", () => {
    for (const field of ["traitor_refusals", "traitor_turns", "recruit_refusals", "recruit_offers"]) {
      const seats = clone(bundledFiles().seats) as { seats: Record<string, number>[] };
      seats.seats[0][field] += 1;
      expect(() => parseBench({ ...bundledFiles(), seats })).toThrow(/count refusals differently/);
    }
  });

  it("a seat row for someone on no board", () => {
    const seats = clone(bundledFiles().seats) as { seats: { entrant: string }[] };
    seats.seats[0].entrant = "bot:heuristic/nobody";
    expect(() => parseBench({ ...bundledFiles(), seats })).toThrow(/on no board/);
  });
});

describe("loadBench", () => {
  it("fetches the four files beside the page and does without profiles", async () => {
    const files = bundledFiles();
    const asked: string[] = [];
    const fetcher = async (url: string) => {
      asked.push(url);
      const name = url.split("/").pop() ?? "";
      const body = name === "profiles.json" ? undefined : files[name.replace(".json", "") as keyof typeof files];
      return { ok: body !== undefined, status: body === undefined ? 404 : 200, json: async () => clone(body) };
    };
    const bench = await loadBench(fetcher, "./");
    expect(asked.sort()).toEqual(["./bench/leaderboard.json", "./bench/matrix.json", "./bench/profiles.json", "./bench/seats.json"]);
    expect(bench.profiles).toBeNull();
    expect(benchUrl("seats.json", "/badactors")).toBe("/badactors/bench/seats.json");
  });

  it("says which file is missing", async () => {
    const fetcher = async () => ({ ok: false, status: 404, json: async () => null });
    await expect(loadBench(fetcher, "/")).rejects.toThrow(/leaderboard\.json \(404\)/);
  });
});

describe("names", () => {
  it("names models as show.py does and bots as bots", () => {
    expect(modelName("anthropic/claude-opus-5.5")).toBe("Claude Opus 5.5");
    expect(modelName("openai/gpt-6.1-sol")).toBe("GPT-6.1 Sol");
    expect(entrantName("bot:heuristic/p3")).toBe("Bot p3");
    expect(labColour("bot:heuristic/p3")).toBe("#8A8F98");
    expect(labColour("anthropic/claude-opus-5.5")).toBe("#D97757");
  });
});
