// A claim's receipt: the words, the stamp, the proof in plain words, and the
// speaker's own diary at the time. Every claim in a fact-checked feed has one,
// and a deep link to it: ?game=seed-56&claim=c927-1.

import type { Game } from "./load";
import { type NameStyle, type Naming, eventQuote, eventText, privateLine } from "./naming";
import type { FeedClaim, FeedEvent, FeedMoment, LineField } from "./types";

export interface ProofLine {
  seq: number;
  day: number;
  phase: FeedEvent["phase"];
  tick: number;
  text: string;
}

export interface ProofGroup {
  title: string;
  lines: ProofLine[];
}

export interface Receipt {
  claim: FeedClaim;
  speaker: string;
  /** The whole thing they said, which the claim was read from. */
  quote: string;
  /** What the claim asserts, in plain words. */
  what: string;
  why: string;
  proof: ProofGroup[];
  /** The speaker's own private line at the time: on the words themselves, or
   * the last one they wrote before. */
  diary: { text: string; field: LineField; tick: number; same: boolean } | null;
  /** The moment the claim belongs to, if it has one. */
  moment: FeedMoment | null;
}

/** How many entries of each kind of proof a receipt lists. */
export const PROOF_LIMIT = 4;

function line(game: Game, seq: number, style: NameStyle): ProofLine {
  const event = game.event(seq);
  const quote = eventQuote(event, style);
  const text = eventText(event, style);
  return {
    seq,
    day: event.day,
    phase: event.phase,
    tick: event.tick,
    text: quote && !text.includes(quote) ? `${text} “${quote}”` : text,
  };
}

const ROUTINE = new Set(["plan_formed", "plan_cut", "replan_capped", "wait", "phase_opened"]);

function group(game: Game, title: string, seqs: number[], style: NameStyle): ProofGroup | null {
  const kept = seqs.filter((seq) => !ROUTINE.has(game.event(seq).type)).slice(0, PROOF_LIMIT);
  return kept.length > 0 ? { title, lines: kept.map((seq) => line(game, seq, style)) } : null;
}

const DIARY_FIELDS: LineField[] = ["diary", "reason", "thoughts"];

function diaryAt(game: Game, claim: FeedClaim, style: NameStyle): Receipt["diary"] {
  const own = game.event(claim.seq);
  for (const field of DIARY_FIELDS) {
    const text = privateLine(own, field, style);
    if (text) return { text, field, tick: own.tick, same: true };
  }
  for (let seq = claim.seq - 1; seq >= 1; seq -= 1) {
    const event = game.event(seq);
    if (event.actor !== claim.speaker) continue;
    const text = privateLine(event, "diary", style);
    if (text) return { text, field: "diary", tick: event.tick, same: false };
  }
  return null;
}

export function claimSaid(claim: FeedClaim, style: NameStyle): string {
  return (style === "model" && claim.audience?.said) || claim.said;
}

export function claimWhat(claim: FeedClaim, style: NameStyle): string {
  return (style === "model" && claim.audience?.what) || claim.what;
}

export function claimWhy(claim: FeedClaim, style: NameStyle): string {
  return (style === "model" && claim.audience?.why) || claim.why;
}

export function receiptFor(game: Game, claim: FeedClaim, style: NameStyle, naming: Naming): Receipt {
  const speaker = naming.name(claim.speaker);
  const proof = [
    group(game, "What really happened", claim.proof, style),
    group(game, `What ${speaker} could actually see`, claim.knew, style),
    group(game, claim.planted_by ? `How ${naming.name(claim.planted_by)} put it there` : "How it got there", claim.path, style),
  ].filter((g): g is ProofGroup => g !== null);
  const moment =
    game.feed.moments.find((m) => m.verdict?.claim === claim.id) ??
    game.feed.moments.find((m) => m.claim === claim.seq) ??
    null;
  return {
    claim,
    speaker,
    quote: eventQuote(game.event(claim.seq), style) ?? claimSaid(claim, style),
    what: claimWhat(claim, style),
    why: claimWhy(claim, style),
    proof,
    diary: diaryAt(game, claim, style),
    moment,
  };
}

/** A receipt in one line, for a share card or a copied link. */
export function receiptLine(game: Game, claim: FeedClaim): string {
  const { castle, seed } = game.feed.game;
  return `${castle} · seed ${seed} · day ${claim.day}, tick ${claim.tick} · claim ${claim.id}`;
}

// ------------------------------------------------------------------ titles

/** Moments in the order the show leads with them: the twist, then by rank. */
export function highlightOrder(moments: FeedMoment[]): FeedMoment[] {
  return [...moments].sort((a, b) => Number(b.kind === "twist_ending") - Number(a.kind === "twist_ending"));
}

/** The diary line an episode is titled by (castle/moments.py title_line). */
export function titleLine(game: Game, style: NameStyle): string | null {
  for (const moment of highlightOrder(game.feed.moments)) {
    if (moment.line?.field === "diary") return privateLine(game.event(moment.line.seq), "diary", style);
  }
  return null;
}
