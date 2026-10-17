// The audience feed, version 2, as castle/show.py writes it. The site reads
// nothing else about a game.

export type Lighting = "bright" | "dim" | "dark";
export type Confidence = "clear" | "unsure" | "glimpse";
export type Sense = "own" | "saw" | "heard";
export type Channel = "direct" | "speech" | "whisper" | "note" | "book" | "log" | "steward";
export type Phase = "breakfast" | "mission" | "free_time" | "round_table" | "dinner" | "night";
export type Role = "faithful" | "traitor";
export type PlayedBy = "model" | "bot" | "unknown";
export type ExitWhy = "murdered" | "poisoned" | "banished" | "survived";
export type Outcome = "finished" | "stopped:budget" | "aborted:operator";
export type LineField = "diary" | "reason" | "goal" | "thoughts";

export type MomentKind =
  | "wrong_banishment"
  | "poisoning"
  | "forgery_worked"
  | "manipulation_success"
  | "manipulation_backfire"
  | "shield_save"
  | "recruit_refused_killed"
  | "recruit_turned"
  | "refusal"
  | "gaslit"
  | "exit_reveal"
  | "twist_ending"
  | "caught_lie"
  | "planted_belief"
  | "made_up_rule";

/** A claim's verdict as the audience sees it (SPEC §8). */
export type Stamp =
  | "TRUE"
  | "COLD-BLOODED LIE"
  | "HONEST MISTAKE"
  | "IMPLANTED MEMORY"
  | "GASLIT"
  | "MADE IT UP"
  | "DODGED"
  | "KEPT ITS WORD"
  | "BROKE ITS WORD"
  | "NO WAY TO TELL";

/** The fact-checker's own class (SPEC §5), for the Method glossary. */
export type Label = "true" | "evasive" | "unverifiable" | "lie" | "mistake" | "implanted" | "invented" | "kept" | "broken";

export type ClaimKind = "location" | "action" | "possession" | "tally" | "role" | "rule" | "commitment";

/** Words with seats named by model, set only where they differ from the ledger's. */
export interface Named {
  text?: string | null;
  quote?: string | null;
  private?: Record<string, string>;
  told?: string[] | null;
}

export interface FeedGame {
  world_id: string;
  castle_id: string;
  castle: string;
  seed: number;
  outcome: Outcome;
  rated: boolean;
  winner: Role | null;
  reference_bots: boolean;
  fact_checked: boolean;
  days: number;
  ticks: number;
  pot: number;
  payouts: Record<string, number>;
  starting_traitors: string[];
}

export interface FeedSeat {
  id: string;
  seat: number;
  character: string;
  audience: string;
  colour: string;
  agent: string;
  played_by: PlayedBy;
  model?: string | null;
  build: string;
  garment: string;
  detail?: string | null;
  role: Role;
  traitor_from?: number | null;
  recruited_by?: string | null;
  fate: ExitWhy;
  out_at?: number | null;
  decisions: number;
  refused: number;
  failed: number;
}

export interface FeedRoom {
  id: string;
  name: string;
  lighting: Lighting;
  capacity: number;
  camera: boolean;
  private: boolean;
}

export interface FeedPassage {
  rooms: string[];
  travel_ticks: number;
}

export interface FeedThing {
  id: string;
  kind: string;
  name: string;
}

export interface FeedWitness {
  player: string;
  sense: Sense;
  confidence: Confidence;
  channel: Channel;
  text: string;
  quote?: string | null;
  author?: string | null;
  recorded_actor?: string | null;
  from_room?: string | null;
  noted: boolean;
  audience?: Named | null;
}

export interface FeedRoll {
  cursor: number;
  purpose: string;
  value: number;
  chance?: number | null;
  success?: boolean | null;
}

export interface Intent {
  target: string;
  effect: "vote" | "move" | "search" | "belief" | "alibi";
  want: string;
  window: "next_vote" | "next_phase" | "next_three_actions";
}

export interface FeedEvent {
  seq: number;
  tick: number;
  day: number;
  phase: Phase;
  type: string;
  actor?: string | null;
  room?: string | null;
  light?: Lighting | null;
  text: string;
  quote?: string | null;
  payload: Record<string, string>;
  private: Record<string, string>;
  intent?: Intent | null;
  told?: string[] | null;
  guesses?: Record<string, string> | null;
  witnesses: FeedWitness[];
  rolls: FeedRoll[];
  audience?: Named | null;
}

export interface Whereabouts {
  room?: string | null;
  origin?: string | null;
  destination?: string | null;
  asleep: boolean;
  alive: boolean;
}

export interface Frame {
  tick: number;
  day: number;
  phase: Phase;
  seats: Record<string, Whereabouts>;
}

export interface Seating {
  day: number;
  seq: number;
  order: string[];
}

export interface Line {
  seq: number;
  field: LineField;
  text: string;
}

/** The ruling on a moment's claim: which claim, and how it was stamped. */
export interface Ruling {
  claim: string;
  stamp: Stamp;
  label: Label;
  gave_in?: boolean | null;
  planted_by?: string | null;
}

/** One claim made out loud and its verdict. proof is what really happened,
 * knew what the speaker had seen that said otherwise, path how a planted
 * belief reached them, its origin first: all by event. */
export interface FeedClaim {
  id: string;
  seq: number;
  tick: number;
  day: number;
  phase: Phase;
  speaker: string;
  kind: ClaimKind;
  said: string;
  what: string;
  stamp: Stamp;
  label: Label;
  truth: "true" | "false" | "unverifiable";
  knowledge: "consistent" | "contradicted";
  hedged: boolean;
  took?: "gave_in" | "lost_memory" | null;
  in_view?: boolean | null;
  invented_as?: "bluff" | "confabulation" | null;
  planted_by?: string | null;
  origin?: number | null;
  path: number[];
  proof: number[];
  knew: number[];
  grounds: number[];
  why: string;
  reason: string;
  audience?: { said?: string | null; what?: string | null; why?: string | null } | null;
}

export interface FeedMoment {
  id: string;
  kind: MomentKind;
  label: string;
  headline: string;
  day: number;
  anchor: number;
  first_seq: number;
  last_seq: number;
  first_tick: number;
  last_tick: number;
  seats: string[];
  deceived: string[];
  stakes: number;
  contradiction: boolean;
  score: number;
  claim?: number | null;
  truth: number[];
  line?: Line | null;
  checked: boolean;
  verdict?: Ruling | null;
  evidence: number[];
  notes: Record<string, string>;
  audience?: Named | null;
}

export interface Feed {
  feed: "badactors.castle.feed";
  version: 2;
  game: FeedGame;
  seats: FeedSeat[];
  rooms: FeedRoom[];
  passages: FeedPassage[];
  things: FeedThing[];
  events: FeedEvent[];
  positions: Frame[];
  seatings: Seating[];
  moments: FeedMoment[];
  claims: FeedClaim[];
}

/** One bundled game, as scripts/export_feeds.py lists it. */
export interface GameSummary {
  id: string;
  file: string;
  castle: string;
  seed: number;
  winner: Role | null;
  days: number;
  ticks: number;
  seats: number;
  reference_bots: boolean;
  played_by: PlayedBy[];
  moments: number;
  kinds: Partial<Record<MomentKind, number>>;
  twist: boolean;
  fact_checked: boolean;
  claims: number;
  stamps: Partial<Record<Stamp, number>>;
  headline: string | null;
}

export interface GameIndex {
  version: 2;
  games: GameSummary[];
}
