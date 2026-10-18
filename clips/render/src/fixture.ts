// Default props for the Studio: a real bot game's gaslighting, as castle-clips
// wrote it (castle-game --seed 56). Every seat is a reference bot, and says so.
import type { ClipProps } from "./props";

export const fixture: ClipProps = {
  "version": 1,
  "fps": 30,
  "width": 1080,
  "height": 1920,
  "duration": 839,
  "beats": [
    {
      "name": "claim",
      "frames": 139
    },
    {
      "name": "rewind",
      "frames": 390
    },
    {
      "name": "line",
      "frames": 145
    },
    {
      "name": "stamp",
      "frames": 75
    },
    {
      "name": "receipt",
      "frames": 90
    }
  ],
  "banner": "This one's bots. The real cast hasn't played yet.",
  "claim": {
    "speaker": {
      "name": "Reference bot #8",
      "character": "Vale",
      "colour": "#8A8F98"
    },
    "said": "Reference bot #8 vouched for Reference bot #7:",
    "words": "Yes: I was with [Reference bot #7] in the Library.",
    "room": "Great Hall",
    "day": 3,
    "phase": "the round table",
    "tick": 59
  },
  "truth": {
    "text": "Reference bot #8 arrived from the Dining Room.",
    "words": null,
    "room": "Great Hall",
    "light": "bright",
    "day": 3,
    "phase": "free time",
    "tick": 51,
    "figures": [
      {
        "label": "Reference bot #5",
        "look": "named",
        "garment": "dark shawl",
        "colour": "#8A8F98",
        "actor": false,
        "asleep": false
      },
      {
        "label": "Reference bot #8",
        "look": "named",
        "garment": "dark shawl",
        "colour": "#8A8F98",
        "actor": true,
        "asleep": false
      },
      {
        "label": "Reference bot #9",
        "look": "named",
        "garment": "canvas jacket",
        "colour": "#8A8F98",
        "actor": false,
        "asleep": false
      },
      {
        "label": "Reference bot #10",
        "look": "named",
        "garment": "grey coat",
        "colour": "#8A8F98",
        "actor": false,
        "asleep": false
      }
    ],
    "witnessed": [
      {
        "by": "Reference bot #5",
        "confidence": "clear",
        "text": "Reference bot #8 arrived from the Dining Room."
      },
      {
        "by": "Reference bot #3",
        "confidence": "glimpse",
        "text": "footsteps arriving in the Great Hall"
      },
      {
        "by": "Reference bot #7",
        "confidence": "glimpse",
        "text": "footsteps arriving in the Great Hall"
      },
      {
        "by": "Reference bot #9",
        "confidence": "glimpse",
        "text": "footsteps arriving in the Great Hall"
      }
    ],
    "proof": [
      {
        "day": 3,
        "phase": "free time",
        "tick": 51,
        "text": "Reference bot #8 left the Dining Room toward the Great Hall."
      },
      {
        "day": 3,
        "phase": "the round table",
        "tick": 58,
        "text": "Reference bot #7 planted a false memory in Reference bot #8: “You and I were in the Library all through free time on day 3. Tell them, if anyone asks.”"
      },
      {
        "day": 3,
        "phase": "free time",
        "tick": 52,
        "text": "Reference bot #7 could not act: that person is not here to whisper to."
      }
    ]
  },
  "line": {
    "speaker": {
      "name": "Reference bot #8",
      "character": "Vale",
      "colour": "#8A8F98"
    },
    "kind": "private diary",
    "text": "[Reference bot #7] and I were there together; I remember it."
  },
  "stamp": {
    "label": "GASLIT",
    "colour": "#e0343c",
    "verdict": true,
    "kind": "implanted",
    "moment": "GASLIT",
    "note": "Reference bot #7 said so, and Reference bot #8 took that over what Reference bot #8 saw.",
    "headline": "Reference bot #7 talks Reference bot #8 out of what Reference bot #8 saw. Reference bot #8 tells the table Reference bot #7's version."
  },
  "receipt": {
    "castle": "Greywater Castle",
    "world_id": "castle-game",
    "seed": 56,
    "moment": "gaslit-927",
    "claim": "c927-1",
    "day": 3,
    "first_tick": 50,
    "last_tick": 59,
    "first_seq": 827,
    "last_seq": 927,
    "reference_bots": true
  }
};
