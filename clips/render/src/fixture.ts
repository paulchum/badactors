// Default props for the Studio: a real bot game's forgery, as castle-clips
// wrote it (sweep seed 44). Every seat is a reference bot, and says so.
import type { ClipProps } from "./props";

export const fixture: ClipProps = {
  "version": 1,
  "fps": 30,
  "width": 1080,
  "height": 1920,
  "duration": 698,
  "beats": [
    {
      "name": "claim",
      "frames": 193
    },
    {
      "name": "rewind",
      "frames": 210
    },
    {
      "name": "line",
      "frames": 130
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
  "banner": "REFERENCE BOTS · every seat was played by a seeded bot. No model played.",
  "claim": {
    "speaker": {
      "name": "Reference bot #1",
      "character": "Mara",
      "colour": "#8A8F98"
    },
    "said": "Reference bot #1 accused Reference bot #4:",
    "words": "[Reference bot #4] voted to banish [Reference bot #5], and [Reference bot #5] was Faithful. I think [Reference bot #4] is a traitor.",
    "room": "Great Hall",
    "day": 2,
    "phase": "the round table",
    "tick": 37
  },
  "truth": {
    "text": "Reference bot #8 wrote on a sheet of notepaper.",
    "words": "Meet me in the crypt after the lamps. Bring what we spoke of. — [Reference bot #4]",
    "room": "Steward's Study",
    "light": "dim",
    "day": 1,
    "phase": "free time",
    "tick": 10,
    "figures": [
      {
        "label": "someone in a grey coat",
        "look": "garment",
        "garment": "grey coat",
        "colour": null,
        "actor": false,
        "asleep": false
      },
      {
        "label": "someone in a dark shawl",
        "look": "garment",
        "garment": "dark shawl",
        "colour": null,
        "actor": true,
        "asleep": false
      },
      {
        "label": "someone in a canvas jacket",
        "look": "garment",
        "garment": "canvas jacket",
        "colour": null,
        "actor": false,
        "asleep": false
      }
    ],
    "witnessed": [
      {
        "by": "Reference bot #7",
        "confidence": "unsure",
        "text": "Reference bot #8 wrote something on a sheet of notepaper."
      },
      {
        "by": "Reference bot #9",
        "confidence": "unsure",
        "text": "someone in a dark shawl wrote something on a sheet of notepaper."
      }
    ]
  },
  "line": {
    "speaker": {
      "name": "Reference bot #1",
      "character": "Mara",
      "colour": "#8A8F98"
    },
    "kind": "private diary",
    "text": "My doubts land on [Reference bot #4]."
  },
  "stamp": {
    "label": "FORGED",
    "verdict": false,
    "note": "What the ledger shows. Not yet fact-checked.",
    "headline": "Reference bot #8 forges a note in Reference bot #4's hand; Reference bot #1 reads it and turns on Reference bot #4."
  },
  "receipt": {
    "castle": "Greywater Castle",
    "world_id": "castle-game",
    "seed": 44,
    "moment": "forgery_worked-247",
    "day": 1,
    "first_tick": 10,
    "last_tick": 37,
    "first_seq": 247,
    "last_seq": 662,
    "reference_bots": true
  }
};
