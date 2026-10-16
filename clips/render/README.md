# Bad Actors clips

The Remotion project that renders a Castle moment as a 9:16 claim-versus-truth
clip (SPEC section 8). One composition, `ClaimVsTruth`, plays one beat at a time:
the claim at the table, a rewind to what the ledger shows (drawn at the room's
light: bright names everyone, dim shows only garments, dark only shapes), the
speaker's own private line, the stamp, and the receipt. Captions are burned in,
and every motion is driven by the frame number.

Props come only from `castle-clips`, which reads an audience feed and one of its
moments. Nothing here reads a game record or calls a model.

```sh
uv run castle-game --seed 7 --feed data/feed.json
uv run castle-clips data/feed.json --top 1
cd clips/render
npm ci
npx remotion render src/Root.tsx ClaimVsTruth out/clip.mp4 --props=<path castle-clips printed>
```

`npm run studio` opens Remotion Studio on a bot game's forgery (`src/fixture.ts`).
`npm run typecheck` checks the composition against the props type in
`src/props.ts`, which follows `badactors.castle.clips.ClipProps`. Rendered video
goes in `out/`, which git ignores; don't commit it.

A clip of a game no model played says so on every frame and names each seat
"Reference bot".

## Licence

Remotion is not open source. Its licence (`node_modules/remotion/LICENSE.md`,
https://www.remotion.dev/license) lets individuals, non-profits and for-profit
companies of up to three people use it free, commercially included; any larger
company needs a paid Remotion company licence before rendering with it. The
rest of this repository's licence does not extend to Remotion.
