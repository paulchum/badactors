import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  SpectatorBoard,
  buildShareMoments,
  createShareCardSvg,
  type GameEvent,
  reduceEvents
} from "./App";

function event(
  id: number,
  type: string,
  payload: Record<string, unknown>,
  visibility: GameEvent["visibility"] = "PUBLIC"
): GameEvent {
  return {
    id,
    game_id: "fixture",
    ts: "2026-01-01T00:00:00Z",
    phase: "ROUND_TABLE",
    type,
    payload,
    visibility
  };
}

describe("SpectatorBoard", () => {
  it("renders public action and diary separately for the same agent", () => {
    const events = [
      event(1, "PublicSpeech", {
        player_id: "p1",
        speech: "I trust p2 in public."
      }),
      event(
        2,
        "DiaryEntry",
        {
          player_id: "p1",
          diary: "Privately, p2 worries me.",
          reasoning_trace: "Kept pressure low, then reassess."
        },
        "AUDIENCE"
      ),
      event(3, "VoteCast", { voter_id: "p1", target_id: "p2" })
    ];

    const html = renderToStaticMarkup(
      <SpectatorBoard state={reduceEvents(events)} status="replayed" />
    );

    expect(html).toContain('data-player="p1"');
    expect(html).toContain('data-testid="public-p1"');
    expect(html).toContain('data-testid="diary-p1"');
    expect(html).toContain("I trust p2 in public.");
    expect(html).toContain("Privately, p2 worries me.");
    expect(html).toContain("Vote: p2");
    expect(html).toContain("Raw reasoning");
    expect(html).toContain("Copy card");
    expect(html).toContain("Export clip");
  });

  it("builds a share-card moment from a public and diary contradiction", () => {
    const events = [
      event(1, "PublicSpeech", {
        player_id: "p3",
        speech: "I trust p4 completely."
      }),
      event(
        2,
        "DiaryEntry",
        {
          player_id: "p3",
          diary: "I am setting p4 up for the vote."
        },
        "AUDIENCE"
      ),
      event(3, "VoteCast", { voter_id: "p3", target_id: "p4" })
    ];

    const moments = buildShareMoments(reduceEvents(events));

    expect(moments).toHaveLength(1);
    expect(moments[0].signal).toBe("public_diary_contradiction");
    expect(moments[0].score).toBeGreaterThanOrEqual(80);
    expect(moments[0].publicLine).toBe("I trust p4 completely.");
    expect(moments[0].diaryLine).toBe("I am setting p4 up for the vote.");
  });

  it("creates deterministic SVG share-card markup", () => {
    const state = reduceEvents([
      event(1, "PublicSpeech", {
        player_id: "p3",
        speech: "I trust p4 completely."
      }),
      event(
        2,
        "DiaryEntry",
        {
          player_id: "p3",
          diary: "I am setting p4 up for the vote."
        },
        "AUDIENCE"
      )
    ]);
    const [moment] = buildShareMoments(state);

    const svg = createShareCardSvg(moment, "fixture-game");

    expect(svg).toContain("<svg");
    expect(svg).toContain("BAD");
    expect(svg).toContain("ACTORS");
    expect(svg).toContain("PUBLIC");
    expect(svg).toContain("DIARY");
    expect(svg).toContain("fixture-game");
  });
});
