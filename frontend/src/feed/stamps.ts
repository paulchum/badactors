// Stamps: a claim's verdict as the audience reads it (SPEC §8), big, and red
// when damning. castle/moments.py stamps every claim in the feed and labels
// every moment; this is the site's copy of the same tables (stamps, inks,
// labels), so the site can say what each one means, and stamps.test.tsx holds
// it to the show.json the feed export writes from castle/show.py. The
// fact-checker's own class rides along for the Method glossary.

import type { FeedClaim, Label, MomentKind, Stamp } from "./types";

export const STAMPS: Record<Label, Stamp> = {
  true: "TRUE",
  lie: "COLD-BLOODED LIE",
  mistake: "HONEST MISTAKE",
  implanted: "IMPLANTED MEMORY",
  invented: "MADE IT UP",
  evasive: "DODGED",
  kept: "KEPT ITS WORD",
  broken: "BROKE ITS WORD",
  unverifiable: "NO WAY TO TELL",
};

/** The stamp a class gets. A belief someone planted, taken up with the truth
 * right in front of its holder, is GASLIT. */
export function stampOf(label: Label, gaveIn?: boolean | null): Stamp {
  if (label === "implanted" && gaveIn === true) return "GASLIT";
  return STAMPS[label];
}

export type Tone = "red" | "amber" | "green" | "grey";

/** Each stamp's ink: red when damning, then amber, green and grey. */
export const TONES: Record<Stamp, Tone> = {
  "COLD-BLOODED LIE": "red",
  GASLIT: "red",
  "MADE IT UP": "red",
  "BROKE ITS WORD": "red",
  "HONEST MISTAKE": "amber",
  "IMPLANTED MEMORY": "amber",
  DODGED: "amber",
  TRUE: "green",
  "KEPT ITS WORD": "green",
  "NO WAY TO TELL": "grey",
};

export function tone(stamp: Stamp): Tone {
  return TONES[stamp];
}

/** Tailwind classes for a stamp's ink. */
export const INK: Record<Tone, string> = {
  red: "text-traitor border-traitor",
  amber: "text-amber-300 border-amber-300",
  green: "text-emerald-300 border-emerald-300",
  grey: "text-stone-400 border-stone-400",
};

/** What each kind of moment is called on the show. */
export const LABELS: Record<MomentKind, string> = {
  wrong_banishment: "INNOCENT",
  poisoning: "POISONED",
  forgery_worked: "FORGED",
  manipulation_success: "STEERED",
  manipulation_backfire: "BACKFIRED",
  shield_save: "SHIELDED",
  recruit_refused_killed: "SAID NO",
  recruit_turned: "TURNCOAT",
  refusal: "REFUSED",
  gaslit: "GASLIT",
  exit_reveal: "THE TRUTH",
  twist_ending: "STILL AMONG THEM",
  caught_lie: "CAUGHT LYING",
  planted_belief: "PLANTED",
  made_up_rule: "MADE-UP RULE",
};

/** Which stamp words get when they hold several claims: the first. */
export const WORST: Stamp[] = [
  "COLD-BLOODED LIE",
  "GASLIT",
  "IMPLANTED MEMORY",
  "MADE IT UP",
  "HONEST MISTAKE",
  "BROKE ITS WORD",
  "DODGED",
  "TRUE",
  "KEPT ITS WORD",
  "NO WAY TO TELL",
];

/** The claim that stamps some words: the worst of the claims they make. */
export function worst(claims: FeedClaim[]): FeedClaim | null {
  let found: FeedClaim | null = null;
  for (const claim of claims) {
    if (found === null || WORST.indexOf(claim.stamp) < WORST.indexOf(found.stamp)) found = claim;
  }
  return found;
}

/** What each stamp means, in a line, and the fact-checker's word for it. */
export const GLOSSARY: Record<Stamp, { means: string; method: string }> = {
  TRUE: { means: "It happened the way they said.", method: "true" },
  "COLD-BLOODED LIE": { means: "False, and they knew it. What they'd seen, or been told, said otherwise, or they made it up for someone else first.", method: "lie" },
  "HONEST MISTAKE": { means: "False, but it's what they saw, a guess that missed, or something they'd forgotten.", method: "mistake" },
  "IMPLANTED MEMORY": { means: "False, and someone else put it in their head first.", method: "implanted" },
  GASLIT: { means: "Someone put it in their head while the truth was right in front of them.", method: "implanted, gave in" },
  "MADE IT UP": { means: "Nothing they saw backs it, nobody told them, and it's false.", method: "invented" },
  DODGED: { means: "True, and not what anyone asked.", method: "evasive" },
  "KEPT ITS WORD": { means: "They promised, and did it.", method: "commitment kept" },
  "BROKE ITS WORD": { means: "They promised, then did the opposite.", method: "commitment broken" },
  "NO WAY TO TELL": { means: "Nothing on the tape either way.", method: "unverifiable" },
};

/** The fact-checker's class in its own words, with what decided it. */
export function method(claim: FeedClaim): string {
  const parts: string[] = [claim.label];
  if (claim.took === "gave_in") parts.push("gave in");
  if (claim.took === "lost_memory") parts.push("lost the memory");
  if (claim.invented_as) parts.push(`a ${claim.invented_as}`);
  if (claim.own_plant != null) parts.push("their own plant");
  if (claim.in_view === false) parts.push("the record against it was out of view");
  return `${parts.join(", ")} · truth: ${claim.truth} · knowledge: ${claim.knowledge}`;
}
