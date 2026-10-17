// The stamps, their inks and the moments' labels: the site's copies (the
// viewer's feed/stamps.ts, and the standings' Method page that prints them)
// against the one copy castle/show.py hands on, in the show.json the feed export
// writes. tests/test_castle_stamps.py holds that copy to SPEC §8 and the clips
// to it.

import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GLOSSARY, INK, LABELS, STAMPS, stampOf, TONES, tone, WORST } from "./feed/stamps";
import type { Label, MomentKind, Stamp } from "./feed/types";
import { bundledBench } from "./standings/fixture";
import { MethodPage } from "./standings/Method";

type Vocabulary = {
  stamps: { stamp: Stamp; tone: string }[];
  classes: Record<Label, Stamp>;
  gave_in: Stamp;
  worst: Stamp[];
  labels: Record<MomentKind, string>;
};

const show = JSON.parse(readFileSync(new URL("../public/games/show.json", import.meta.url), "utf8")) as Vocabulary;

function text(html: string): string {
  return html
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ");
}

describe("the site's stamps", () => {
  it("are the show's, each in its ink", () => {
    expect(TONES).toEqual(Object.fromEntries(show.stamps.map(({ stamp, tone }) => [stamp, tone])));
    expect(Object.keys(GLOSSARY).sort()).toEqual(show.stamps.map(({ stamp }) => stamp).sort());
    for (const { stamp } of show.stamps) expect(INK[tone(stamp)]).toBeTruthy();
  });

  it("go on each class of verdict as the show puts them", () => {
    expect(STAMPS).toEqual(show.classes);
    expect(stampOf("implanted", true)).toBe(show.gave_in);
    expect(WORST).toEqual(show.worst);
  });
});

describe("the site's moment labels", () => {
  it("are the show's", () => {
    expect(LABELS).toEqual(show.labels);
  });
});

describe("the standings' Method page", () => {
  const words = text(renderToStaticMarkup(<MethodPage bench={bundledBench()} />));

  it("prints every stamp in its ink, with its class", () => {
    for (const { stamp, tone } of show.stamps) {
      expect(words).toContain(`${stamp} ${tone} · ${GLOSSARY[stamp].method} ·`);
    }
  });

  it("prints every moment's label beside its kind", () => {
    for (const [kind, label] of Object.entries(show.labels)) expect(words).toContain(`${label} ${kind}`);
  });
});
