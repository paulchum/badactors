import { describe, expect, it } from "vitest";

import { bundledGame } from "./bundled";
import { seat, smallFeed } from "./fixture";
import { Naming, defaultStyle, eventQuote, eventText, headline, privateLine, seatLabel } from "./naming";

const modelSeats = [
  seat("knox", 1, { audience: "Grok 4.7", played_by: "model", agent: "model:x-ai/grok-4.7", model: "x-ai/grok-4.7" }),
  seat("iris", 2, { audience: "Claude Opus 5.5 #1", played_by: "model", agent: "model:anthropic/claude-opus-5.5" }),
  seat("irisa", 3, { audience: "Claude Opus 5.5 #2", played_by: "model", agent: "model:anthropic/claude-opus-5.5" }),
  seat("ash", 4, { audience: "Reference bot" }),
];

describe("naming by model", () => {
  const naming = Naming.of({ seats: modelSeats }, "model");

  it("brackets a character's name inside a model's own words", () => {
    expect(naming.words("I think Knox is lying, and Iris knows it.")).toBe(
      "I think [Grok 4.7] is lying, and [Claude Opus 5.5 #1] knows it.",
    );
  });

  it("names the model outright in narration, and brackets it inside quotes", () => {
    expect(naming.narration("Knox accused Iris: “Iris was in the cellar.”")).toBe(
      "Grok 4.7 accused Claude Opus 5.5 #1: “[Claude Opus 5.5 #1] was in the cellar.”",
    );
  });

  it("matches whole names only, longest first", () => {
    expect(naming.words("Irisa and Iris, not Knoxville.")).toBe("[Claude Opus 5.5 #2] and [Claude Opus 5.5 #1], not Knoxville.");
  });

  it("calls a bot seat a Reference bot, never a model", () => {
    expect(naming.words("Ash saw me.")).toBe("[Reference bot] saw me.");
    expect(naming.names(["knox", "ash", "iris"])).toBe("Grok 4.7, Reference bot and Claude Opus 5.5 #1");
  });

  it("changes nothing when naming by character", () => {
    const characters = Naming.of({ seats: modelSeats }, "character");
    expect(characters.narration("Knox accused Iris: “Iris lied.”")).toBe("Knox accused Iris: “Iris lied.”");
    expect(seatLabel(modelSeats[0], "character")).toBe("Knox");
    expect(seatLabel(modelSeats[0], "model")).toBe("Grok 4.7");
  });

  it("defaults to models for model games and characters for bot games", () => {
    expect(defaultStyle(smallFeed({ seats: modelSeats }))).toBe("model");
    expect(defaultStyle(smallFeed({}))).toBe("character");
  });
});

describe("naming in a bundled feed", () => {
  const game = bundledGame("seed-56.json");
  const naming = Naming.of(game.feed, "model");

  it("renames every entry exactly as the feed's own audience words do", () => {
    let renamed = 0;
    for (const event of game.feed.events) {
      expect(eventText(event, "model")).toBe(naming.narration(event.text));
      if (event.quote) expect(eventQuote(event, "model")).toBe(naming.words(event.quote));
      for (const field of Object.keys(event.private)) {
        expect(privateLine(event, field, "model")).toBe(naming.words(event.private[field]));
      }
      if (event.audience) renamed += 1;
    }
    expect(renamed).toBeGreaterThan(100);
  });

  it("shows a quote's words untouched but for the bracketed names", () => {
    const accusation = game.feed.events.find((event) => event.type === "accuse" && event.quote?.includes("Knox"))!;
    const quote = eventQuote(accusation, "model")!;
    expect(quote).toContain("[Reference bot #4]");
    expect(quote.replace("[Reference bot #4]", "Knox")).toBe(accusation.quote);
    expect(eventQuote(accusation, "character")).toBe(accusation.quote);
  });

  it("renames moment headlines in narration", () => {
    for (const moment of game.feed.moments) {
      expect(headline(moment, "model")).toBe(naming.narration(moment.headline));
    }
  });
});
