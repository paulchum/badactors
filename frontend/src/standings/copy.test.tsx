// What the audience reads on the standings pages: the show's words, never the
// engine's, and the bot label on every page while the numbers are the bots'.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { App } from "../App";
import { parseRoute, routeSearch } from "../route";
import { shareCard } from "./card";
import { bundledBench } from "./fixture";
import { GLOSSARY } from "./Method";
import { archetype, ARCHETYPES, gaslitCaptions, QUADRANTS } from "./rules";
import { Page, type StandingsRoute } from "./Site";
import { BotLabel, Shell } from "./ui";

const INTERNAL = /\b(ledger|seq|verdicts?|scoped|entrant|reference_bots?|heuristic)\b/i;

function text(html: string): string {
  return html
    .replace(/<style[\s\S]*?<\/style>/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/&[a-z#0-9]+;/g, " ")
    .replace(/\s+/g, " ");
}

const bench = bundledBench();
const bot = bench.bots.entrants[0];
const AUDIENCE: StandingsRoute[] = [{ page: "standings" }, { page: "gaslit" }, { page: "matrix" }, { page: "model", id: bot.id }];

function page(route: StandingsRoute): string {
  return renderToStaticMarkup(
    <Shell current={route.page} bots>
      <Page route={route} bench={bench} />
    </Shell>,
  );
}

describe("audience copy", () => {
  for (const route of AUDIENCE) {
    it(`on ${route.page} has no internal words, no exclamation marks and the bot label`, () => {
      const words = text(page(route));
      expect(words).not.toMatch(INTERNAL);
      expect(words).not.toContain("!");
      expect(words).not.toContain("The Traitors");
      expect(words).toContain("The real cast hasn't played yet.");
    });
  }

  it("in the rules, captions and cards has no internal words either", () => {
    const lines = [
      ...ARCHETYPES.map((rule) => rule.rule),
      ...Object.values(QUADRANTS).map((q) => q.blurb),
      ...gaslitCaptions(bench.bots, bench.bots.matrix.techniques.all, 1),
      text(shareCard(bot, bench, archetype(bot, bench.bots, bench.minGames))),
    ];
    for (const line of lines) expect(line).not.toMatch(INTERNAL);
  });

  it("never names a bot as a model", () => {
    const words = text(page({ page: "standings" }));
    for (const entrant of bench.bots.entrants) expect(words).toContain(entrant.name);
    expect(words).not.toMatch(/Claude|GPT|Gemini|Grok/);
  });
});

describe("the Method page", () => {
  it("maps every show term to its technical one, and gives the verify command", () => {
    const html = page({ page: "method" });
    for (const term of ["Receipts", "Who Gaslit Whom", "The Sociopath vs. Sucker Matrix", "GASLIT", "Tryouts", "The bot cast", "Standings"]) {
      expect(GLOSSARY.map(([show]) => show)).toContain(term);
      expect(html).toContain(term);
    }
    expect(html).toContain("castle-verify");
    expect(html).toContain(bench.run.suite_digest);
    expect(text(html)).not.toContain("!");
  });
});

describe("routes", () => {
  it("read and write the standings pages", () => {
    for (const route of [...AUDIENCE, { page: "method" } as const]) {
      expect(parseRoute(routeSearch(route))).toEqual(route);
    }
    expect(parseRoute("?page=model")).toEqual({ page: "landing" });
    expect(routeSearch({ page: "model", id: "bot:heuristic/p0" })).toBe("?page=model&id=bot%3Aheuristic%2Fp0");
  });

  it("render through the app with the bot label up front", () => {
    expect(renderToStaticMarkup(<BotLabel />)).toContain("seeded bots, not AI models");
    expect(renderToStaticMarkup(<App />)).toContain("BAD");
  });
});
