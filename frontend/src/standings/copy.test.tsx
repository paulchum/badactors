// What the audience reads on the standings pages: the show's words, never the
// engine's, and the bot label on every page while the numbers are the bots'.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { App } from "../App";
import { parseRoute, routeSearch } from "../route";
import { shareCard } from "./card";
import { bundledBench, entrant } from "./fixture";
import { GLOSSARY } from "./Method";
import { archetype, ARCHETYPES, gaslitCaptions, QUADRANTS } from "./rules";
import { Page, type StandingsRoute } from "./Site";
import { declined, turnedDown } from "./Standings";
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
      expect(words).not.toContain(["The", "Traitors"].join(" "));
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

describe("the two refusals", () => {
  it("say the villain role declined in turns as a traitor, and the killers turned down in offers", () => {
    const refuser = entrant("bot:x/a", 6, (i) =>
      i === 0 ? { traitor: true, traitor_turns: 40, traitor_refusals: 2, recruit_offers: 0, ticks: { declined: [5, 9] } } : { recruit_offers: 1, recruit_refusals: 1, ticks: { turned_down: [30, 30] } },
    );
    expect(declined(refuser)).toBe("2 of 40 turns as a traitor");
    expect(turnedDown(refuser)).toBe("5 of 5 offers");
    expect(refuser.stats?.declined.receipts.map((r) => r.ticks)).toEqual([[5, 9]]);
    const quiet = entrant("bot:x/b", 6, (i) => (i === 0 ? { recruit_offers: 1 } : {}));
    expect(declined(quiet)).toBe("never a traitor");
    expect(turnedDown(quiet)).toBe("0 of 1 offer");
    expect(turnedDown(entrant("bot:x/c", 6))).toBe("never asked");
  });

  it("are on the standings and every rap sheet, apart", () => {
    const standings = text(page({ page: "standings" }));
    expect(standings).toContain("Declined the villain role");
    expect(standings).toContain("Turned down the killers");
    expect(standings).toContain(declined(bot));
    for (const entrant of bench.bots.entrants) {
      const sheet = text(page({ page: "model", id: entrant.id }));
      expect(sheet).toContain("Declined the villain role");
      expect(sheet).toContain("Turned down the killers");
    }
  });
});

describe("the Method page", () => {
  it("defines both refusals and how an episode is numbered and titled", () => {
    const shows = GLOSSARY.map(([show]) => show);
    for (const term of ["Declined the villain role", "Turned down the killers", "Episode"]) expect(shows).toContain(term);
    const words = text(page({ page: "method" }));
    expect(words).toContain("traitor_refusals.rate");
    expect(words).toContain("recruit_refusals");
    expect(words).toContain("Two numbers, never one.");
    expect(words).toContain("numbered by its seed and titled by the diary line on the moment the show leads with");
    expect(words).toContain("Nights 0 to 199");
  });

  it("maps every show term to its technical one, and gives the verify command", () => {
    const html = page({ page: "method" });
    for (const term of ["Receipts", "Who Gaslit Whom", "The Sociopath vs. Sucker Matrix", "Tryouts", "The bot cast", "Standings"]) {
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
