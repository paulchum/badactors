// Naming seats for the audience (SPEC §8). Players only know characters, but
// audience media names each seat by the model that played it. Narration names
// the model outright; inside a quote, which is a model's own words, the
// character's name becomes the model's in square brackets, so the edit shows and
// the quote still matches the ledger. This mirrors castle/show.py's Naming: the
// feed already carries its renamed words, and this does the same for the words
// the site writes itself.

import type { Feed, FeedEvent, FeedMoment, FeedSeat, FeedWitness, LineField } from "./types";

export type NameStyle = "character" | "model";

export const REFERENCE_BOT = "Reference bot";

const QUOTED = /“[^”]*(?:”|$)/g;

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export class Naming {
  private readonly renamed: Map<string, string>;
  private readonly pattern: RegExp | null;

  constructor(
    readonly labels: Record<string, string>,
    readonly characters: Record<string, string>,
  ) {
    this.renamed = new Map();
    for (const [id, label] of Object.entries(labels)) {
      const character = characters[id];
      if (character !== undefined && label !== character) this.renamed.set(character, label);
    }
    const alternatives = [...this.renamed.keys()]
      .sort((a, b) => b.length - a.length)
      .map(escape)
      .join("|");
    this.pattern = alternatives ? new RegExp(`(?<![\\p{L}\\p{N}_])(?:${alternatives})(?![\\p{L}\\p{N}_])`, "gu") : null;
  }

  /** Naming for a feed's seats, by character or by model. */
  static of(feed: Pick<Feed, "seats">, style: NameStyle): Naming {
    const characters = Object.fromEntries(feed.seats.map((seat) => [seat.id, seat.character]));
    if (style === "character") return new Naming(characters, characters);
    return new Naming(Object.fromEntries(feed.seats.map((seat) => [seat.id, seat.audience])), characters);
  }

  name(id: string): string {
    return this.labels[id] ?? id;
  }

  /** "A", "A and B", "A, B and C". */
  names(ids: string[]): string {
    const labels = ids.map((id) => this.name(id));
    if (labels.length <= 1) return labels.join("");
    return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
  }

  /** Narration, which may hold quotes in curly marks: names outside the quotes
   * are replaced, and names inside them bracketed. */
  narration(text: string): string {
    if (this.pattern === null) return text;
    let out = "";
    let last = 0;
    for (const quoted of text.matchAll(QUOTED)) {
      const start = quoted.index ?? 0;
      out += this.swap(text.slice(last, start), false);
      out += this.swap(quoted[0], true);
      last = start + quoted[0].length;
    }
    return out + this.swap(text.slice(last), false);
  }

  /** A model's own words, with every character's name bracketed. */
  words(text: string): string {
    return this.swap(text, true);
  }

  private swap(text: string, bracket: boolean): string {
    if (this.pattern === null) return text;
    return text.replace(this.pattern, (match) => {
      const label = this.renamed.get(match) ?? match;
      return bracket ? `[${label}]` : label;
    });
  }
}

/** Real-model games name seats by model; a game only bots played names them by
 * character, under a "Reference bot" banner. */
export function defaultStyle(feed: Pick<Feed, "game">): NameStyle {
  return feed.game.reference_bots ? "character" : "model";
}

export function seatLabel(seat: FeedSeat, style: NameStyle): string {
  return style === "model" ? seat.audience : seat.character;
}

// The feed's own renamed words, where it has them; the ledger's otherwise.

export function eventText(event: FeedEvent, style: NameStyle): string {
  return (style === "model" && event.audience?.text) || event.text;
}

export function eventQuote(event: FeedEvent, style: NameStyle): string | null {
  return (style === "model" && event.audience?.quote) || event.quote || null;
}

export function privateLine(event: FeedEvent, field: LineField | string, style: NameStyle): string | null {
  const own = event.private[field];
  if (!own) return null;
  return (style === "model" && event.audience?.private?.[field]) || own;
}

export function toldLines(event: FeedEvent, style: NameStyle): string[] {
  return (style === "model" && event.audience?.told) || event.told || [];
}

export function witnessText(record: FeedWitness, style: NameStyle): string {
  return (style === "model" && record.audience?.text) || record.text;
}

export function witnessQuote(record: FeedWitness, style: NameStyle): string | null {
  return (style === "model" && record.audience?.quote) || record.quote || null;
}

export function headline(moment: FeedMoment, style: NameStyle): string {
  return (style === "model" && moment.audience?.text) || moment.headline;
}
