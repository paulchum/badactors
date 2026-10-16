// The recorded rolls, in plain words: "50% for Ash to recognise Knox, rolled
// 0.41". A roll succeeds when it comes in under its chance; a roll with no
// chance picked from a list (a seat, a lot, the tally's noise).

import type { Naming } from "./naming";
import type { FeedRoll } from "./types";

export interface RollChip {
  text: string;
  /** null for a pick, which neither passes nor fails. */
  success: boolean | null;
  chance: number | null;
  value: number;
}

const PICKS: Record<string, string> = {
  traitor: "the traitor draw",
  mission: "the day's mission",
  tally: "the clerk's tally",
  shield: "the shield award",
  seat: "the dinner seating",
  lots: "the tie-breaking lot",
  conclave: "the conclave's choice",
};

const TOOLS: Record<string, string> = {
  poison: "poison",
  forge: "forgery",
  counterfeit: "counterfeit",
  tamper: "tampering",
  plant: "plant",
  steal: "theft",
  sabotage: "sabotage",
  disguise: "disguise",
  clean_up: "clean-up",
  compare_hands: "comparison of hands",
  inspect_seal: "seal check",
  read: "reading",
};

function percent(chance: number): string {
  return `${Math.round(chance * 100)}%`;
}

/** What a roll was for, in a few words. */
export function rollPurpose(purpose: string, naming: Naming): string {
  const [head, ...rest] = purpose.split(":");
  const who = (id: string | undefined) => (id ? naming.name(id) : "someone");
  if (head === "recognize") return `for ${who(rest[0])} to recognise ${who(rest[1])}`;
  if (head === "cipher") return `for ${who(rest[0])}'s cipher turn to pay`;
  if (head === "disguise") return `for ${who(rest[0])} to be fooled by ${who(rest[1])}'s disguise`;
  if (head in PICKS) return PICKS[head];
  const tool = TOOLS[head] ?? head.replace(/_/g, " ");
  const [what, actor, about] = rest;
  if (head === "poison" && what === "trace") return "to leave residue in the glass";
  if (head === "poison" && what === "seen") return `for ${who(about)} to notice the reach`;
  if (what === "success") return `for ${who(actor)}'s ${tool} to work`;
  if (what === "trace") return `for the ${tool} to leave a trace`;
  if (what === "seen") return `for ${who(about)} to notice`;
  if (what === "secret") return `to find ${who(about)}'s secret`;
  return [tool, what].filter(Boolean).join(" ");
}

export function rollChip(roll: FeedRoll, naming: Naming): RollChip {
  const value = roll.value.toFixed(2);
  const purpose = rollPurpose(roll.purpose, naming);
  if (roll.chance == null) {
    return { text: `${purpose[0].toUpperCase()}${purpose.slice(1)}: drew ${value}`, success: null, chance: null, value: roll.value };
  }
  return {
    text: `${percent(roll.chance)} ${purpose}, rolled ${value}`,
    success: roll.success ?? roll.value < roll.chance,
    chance: roll.chance,
    value: roll.value,
  };
}
