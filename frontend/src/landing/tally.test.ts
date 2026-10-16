import { describe, expect, it } from "vitest";

import { sendPick } from "./tally";

describe("the human tally", () => {
  it("asks nothing when no endpoint is configured", async () => {
    let asked = false;
    const post = async () => {
      asked = true;
      return { ok: true, json: async () => ({}) };
    };
    expect(await sendPick("", "seed-56:3-906", "wren", post)).toBeNull();
    expect(asked).toBe(false);
  });

  it("posts the pick and reads back every pick so far", async () => {
    const sent: { url: string; body: string; method: string }[] = [];
    const post = async (url: string, init: { method: string; body: string }) => {
      sent.push({ url, body: init.body, method: init.method });
      return { ok: true, json: async () => ({ picks: { wren: 41, june: 7, hale: -1, nell: "x" } }) };
    };
    const picks = await sendPick("https://tally.example/pick", "seed-56:3-906", "wren", post);
    expect(sent).toEqual([{ url: "https://tally.example/pick", method: "POST", body: '{"puzzle":"seed-56:3-906","pick":"wren"}' }]);
    expect(picks).toEqual({ wren: 41, june: 7 });
  });

  it("falls back to the table's votes when the endpoint fails", async () => {
    const down = async () => {
      throw new Error("offline");
    };
    const refused = async () => ({ ok: false, json: async () => ({}) });
    const garbled = async () => ({ ok: true, json: async () => ({ nope: 1 }) });
    for (const post of [down, refused, garbled]) {
      expect(await sendPick("https://tally.example/pick", "p", "wren", post)).toBeNull();
    }
  });
});
