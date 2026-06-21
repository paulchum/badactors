import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SpectatorBoard, type GameEvent, reduceEvents } from "./App";

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
  });
});
