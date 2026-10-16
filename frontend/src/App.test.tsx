import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renders the brand and the castle commands", () => {
    const html = renderToStaticMarkup(<App />);

    expect(html).toContain("BAD");
    expect(html).toContain("ACTORS");
    expect(html).toContain("The Castle");
    expect(html).toContain("uv run castle-game --seed 7");
    expect(html).toContain("uv run castle-sweep");
    expect(html).toContain("scripts/castle_three_days.py");
  });
});
