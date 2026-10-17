import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { bundledGame, bundledIndex } from "./feed/bundled";
import { Naming } from "./feed/naming";
import { dailyPick, puzzleOfRound, puzzlesFrom, score } from "./feed/puzzle";
import { receiptFor, titleLine } from "./feed/receipt";
import { GLOSSARY, STAMPS, WORST, stampOf, worst } from "./feed/stamps";
import { Reveal, Stage } from "./landing/Landing";
import { parseRoute, routeSearch } from "./route";
import { ClaimReceipt, receiptHref } from "./viewer/Receipt";
import { Replay } from "./viewer/Viewer";

const index = bundledIndex();
const summary = index.games.find((g) => g.id === "seed-56")!;
const game = bundledGame(summary.file);
const gaslit = game.feed.claims.find((c) => c.stamp === "GASLIT")!;

function text(html: string): string {
  return html
    .replace(/<[^>]*>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ");
}

/** Builders' words, which nothing the audience reads may use. A "ledger page" is
 * a thing in the castle, not a builder's word. */
const BANNED = /\b(ledger(?! page)|seq|entry|entries|scoped?|verdicts?)\b/i;

describe("stamps", () => {
  it("are the fact-checker's classes, gaslit when they gave in", () => {
    expect(stampOf("lie")).toBe("COLD-BLOODED LIE");
    expect(stampOf("mistake")).toBe("HONEST MISTAKE");
    expect(stampOf("implanted")).toBe("IMPLANTED MEMORY");
    expect(stampOf("implanted", true)).toBe("GASLIT");
    expect(stampOf("implanted", false)).toBe("IMPLANTED MEMORY");
    expect(stampOf("invented")).toBe("MADE IT UP");
    expect(stampOf("evasive")).toBe("DODGED");
    expect(stampOf("true")).toBe("TRUE");
    expect(new Set(Object.keys(GLOSSARY))).toEqual(new Set(WORST));
  });

  it("match the stamp castle/moments.py put on every bundled claim", () => {
    for (const s of index.games) {
      const feed = bundledGame(s.file).feed;
      expect(feed.claims.length, s.id).toBe(s.claims);
      for (const claim of feed.claims) {
        expect(stampOf(claim.label, claim.took === "gave_in")).toBe(claim.stamp);
        expect(Object.values(STAMPS)).toContain(claim.stamp === "GASLIT" ? "IMPLANTED MEMORY" : claim.stamp);
      }
      for (const moment of feed.moments) {
        if (!moment.verdict || moment.claim == null) continue;
        const on = feed.claims.filter((c) => c.seq === moment.claim);
        expect(worst(on)?.id).toBe(moment.verdict.claim);
      }
    }
  });
});

describe("a claim's receipt", () => {
  it("is a deep link", () => {
    const link = receiptHref("seed-56", gaslit);
    expect(link).toBe(`?game=seed-56&tick=${gaslit.tick}&claim=${gaslit.id}`);
    const route = parseRoute(link);
    expect(route).toMatchObject({ page: "viewer", game: "seed-56", claim: gaslit.id });
    expect(routeSearch(route)).toBe(link);
  });

  it("holds the quote, the stamp, the proof in plain words and the diary", () => {
    const naming = Naming.of(game.feed, "character");
    const receipt = receiptFor(game, gaslit, "character", naming);
    expect(receipt.proof.map((g) => g.title)).toEqual([
      "What really happened",
      `What ${naming.name(gaslit.speaker)} could actually see`,
      `How ${naming.name(gaslit.planted_by!)} put it there`,
    ]);
    expect(receipt.diary?.same).toBe(true);
    const html = renderToStaticMarkup(
      <ClaimReceipt game={game} gameId="seed-56" claim={gaslit} style="character" naming={naming} />,
    );
    const shown = text(html);
    expect(shown).toContain(game.event(gaslit.seq).quote!);
    expect(shown).toContain("GASLIT");
    expect(shown).toContain(gaslit.why);
    expect(shown).toContain(receipt.diary!.text);
    for (const group of receipt.proof) for (const line of group.lines) expect(shown).toContain(line.text);
    expect(html).toContain(`claim=${gaslit.id}`);
    expect(html).toContain(`games/cards/seed-56/${gaslit.id}.svg`);
    expect(shown).toContain(`seed 56 · day ${gaslit.day}, tick ${gaslit.tick} · claim ${gaslit.id}`);
  });

  it("opens in the replay from the link, on its moment", () => {
    const route = { page: "viewer" as const, game: "seed-56", tick: null, moment: null, claim: gaslit.id };
    const html = renderToStaticMarkup(<Replay game={game} summary={summary} route={route} go={() => undefined} />);
    expect(html).toContain("The receipt");
    expect(html).toContain("GASLIT");
    expect(html).toContain(`tick ${gaslit.tick} of`);
  });
});

describe("audience copy", () => {
  it("never says ledger, seq, entry, scoped or verdict", () => {
    const shown: string[] = [];
    for (const s of index.games) {
      const g = bundledGame(s.file);
      for (const moment of g.feed.moments.slice(0, 6)) {
        const route = { page: "viewer" as const, game: s.id, tick: null, moment: moment.id, claim: g.feed.claims[0]?.id ?? null };
        shown.push(text(renderToStaticMarkup(<Replay game={g} summary={s} route={route} go={() => undefined} />)));
      }
      const puzzle = puzzlesFrom(g)[0];
      const naming = Naming.of(g.feed, "character");
      shown.push(text(renderToStaticMarkup(<Stage game={g} summary={s} puzzle={puzzle} number={1} />)));
      for (const suspect of puzzle.suspects) {
        shown.push(
          text(
            renderToStaticMarkup(
              <Reveal
                game={g}
                summary={s}
                puzzle={puzzle}
                result={score(puzzle, suspect.id)}
                number={1}
                naming={naming}
                style="character"
                picks={null}
              />,
            ),
          ),
        );
      }
      for (const claim of g.feed.claims.filter((c) => c.stamp !== "TRUE")) {
        shown.push(
          text(renderToStaticMarkup(<ClaimReceipt game={g} gameId={s.id} claim={claim} style="model" naming={Naming.of(g.feed, "model")} />)),
        );
      }
    }
    for (const page of shown) {
      const found = page.match(BANNED);
      expect(found, found ? page.slice(Math.max(0, (found.index ?? 0) - 80), (found.index ?? 0) + 80) : "").toBeNull();
    }
  });
});

describe("the landing reveal", () => {
  const daily = dailyPick(index.games, 0);
  const g = bundledGame(daily.game.file);
  const puzzle = puzzleOfRound(puzzlesFrom(g), daily.round);
  const naming = Naming.of(g.feed, "character");
  const reveal = (pick: string) =>
    text(
      renderToStaticMarkup(
        <Reveal game={g} summary={daily.game} puzzle={puzzle} result={score(puzzle, pick)} number={1} naming={naming} style="character" picks={null} />,
      ),
    );

  it("stamps the lie with its real verdict and links its receipt", () => {
    const shown = reveal(puzzle.liar);
    const claim = g.claim(puzzle.claim)!;
    expect(claim.stamp).toBe("COLD-BLOODED LIE");
    expect(shown).toContain("COLD-BLOODED LIE");
    expect(shown).toContain(claim.why);
    expect(shown).toContain("See the receipt");
    expect(shown).not.toMatch(/fact-checker has not|not fact-checked/i);
    expect(shown).toContain("A new table every night.");
    expect(shown).toMatch(/You caught it\. \d+ of \d+ players at the table did\./);
  });

  it("says who fooled you when you pick wrong", () => {
    const wrong = puzzle.suspects.find((s) => !s.traitor)!;
    expect(reveal(wrong.id)).toMatch(
      new RegExp(`You got fooled by ${naming.name(puzzle.liar)}\\. \\d+ of \\d+ players at the table caught it\\.`),
    );
  });

  it("names suspects by character on a bot game", () => {
    const html = renderToStaticMarkup(<Stage game={g} summary={daily.game} puzzle={puzzle} number={1} />);
    expect(html).not.toContain("Reference bot #");
    expect(titleLine(g, "character")).not.toBeNull();
  });
});
