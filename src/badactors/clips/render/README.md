# Bad Actors clip renderer

Remotion project for vertical clips. The Python detector writes ranked moments; this
project renders a selected moment as PUBLIC first, DIARY as the payoff, and a
small event-caption rail.

Headless rendering requires installing the local package dependencies in this
directory and a working Chromium environment:

```bash
npm install
npm run render -- --props=moment.json
```
