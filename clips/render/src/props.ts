// The props castle-clips writes (badactors.castle.clips.ClipProps), version 1.
// The Python models are the source of truth; these types follow them.

export type BeatName = "claim" | "rewind" | "line" | "stamp" | "receipt";
export type Lighting = "bright" | "dim" | "dark";
export type Look = "named" | "garment" | "shape";
export type Confidence = "clear" | "unsure" | "glimpse";

export type Beat = { name: BeatName; frames: number };

export type Speaker = { name: string; character: string; colour: string };

export type ClaimBeat = {
  speaker: Speaker;
  said: string;
  words: string;
  room: string;
  day: number;
  phase: string;
  tick: number;
};

export type Figure = {
  label: string;
  look: Look;
  garment: string | null;
  colour: string | null;
  actor: boolean;
  asleep: boolean;
};

export type Witnessed = { by: string; confidence: Confidence; text: string };

export type TruthBeat = {
  text: string;
  words: string | null;
  room: string;
  light: Lighting;
  day: number;
  phase: string;
  tick: number;
  figures: Figure[];
  witnessed: Witnessed[];
};

export type LineBeat = { speaker: Speaker; kind: string; text: string };

export type Stamp = {
  label: string;
  verdict: boolean;
  note: string;
  headline: string;
};

export type Receipt = {
  castle: string;
  world_id: string;
  seed: number;
  moment: string;
  day: number;
  first_tick: number;
  last_tick: number;
  first_seq: number;
  last_seq: number;
  reference_bots: boolean;
};

export type ClipProps = {
  version: 1;
  fps: number;
  width: number;
  height: number;
  duration: number;
  beats: Beat[];
  banner: string | null;
  claim: ClaimBeat;
  truth: TruthBeat;
  line: LineBeat;
  stamp: Stamp;
  receipt: Receipt;
};

/** Where each beat starts, in frames, in the order the props give them. */
export function schedule(beats: Beat[]): Record<BeatName, { from: number; frames: number }> {
  const out = {} as Record<BeatName, { from: number; frames: number }>;
  let from = 0;
  for (const beat of beats) {
    out[beat.name] = { from, frames: beat.frames };
    from += beat.frames;
  }
  return out;
}
