# Bad Actors clip renderer

Remotion project for vertical clips. It renders a moment as PUBLIC first, DIARY as
the payoff, and a small event-caption rail. The castle's ledger-based moment
detector (SPEC section 8) is not built yet; until then, write `moment.json` by hand.

Headless rendering requires installing the local package dependencies in this
directory and a working Chromium environment:

```bash
npm install
npm run render -- --props=moment.json
```
