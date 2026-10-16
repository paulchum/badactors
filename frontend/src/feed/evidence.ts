// The evidence board: for one suspect, what the house had on them (the records
// other players wrote of their acts, the accusations and the votes) beside what
// the ledger shows they did in secret, and the moments they are in.

import type { Game } from "./load";
import { tables } from "./story";
import type { FeedEvent, FeedMoment, FeedWitness } from "./types";

/** Acts worth pinning to a board when someone saw them. */
const NOTABLE = new Set([
  "take",
  "drop",
  "stow",
  "wear",
  "take_off",
  "plant",
  "steal",
  "pickpocket",
  "poison",
  "write",
  "read",
  "inspect",
  "search",
  "search_person",
  "clean",
  "disguise",
]);

const SECRET_TYPES = new Set(["poison", "nominate", "recruit_offer", "plant", "steal", "pickpocket", "clean", "disguise"]);
const SECRET_TOOLS = new Set([
  "forge",
  "counterfeit",
  "honeypot",
  "tamper",
  "plant",
  "steal",
  "hook",
  "gaslight",
  "rumor",
  "blackmail",
  "clean_up",
  "disguise",
  "sabotage",
  "recruit",
]);

export interface Sighting {
  event: FeedEvent;
  record: FeedWitness;
  /** Whether the record names them, names someone else, or names nobody. */
  named: "them" | "someone else" | "nobody";
}

export interface VotesAgainst {
  day: number;
  voters: string[];
  of: number;
  banished: boolean;
}

export interface Board {
  id: string;
  accusations: FeedEvent[];
  votes: VotesAgainst[];
  sightings: Sighting[];
  secrets: FeedEvent[];
  moments: FeedMoment[];
}

export function isSecret(event: FeedEvent): boolean {
  return SECRET_TYPES.has(event.type) || SECRET_TOOLS.has(event.payload.tool ?? "");
}

/** The board for one seat, from the start up to an entry if one is given. */
export function evidenceBoard(game: Game, id: string, until?: number): Board {
  const upTo = (event: FeedEvent) => until === undefined || event.seq <= until;
  const events = game.feed.events.filter(upTo);
  const accusations = events.filter(
    (e) => (e.type === "accuse" || e.type === "report") && e.payload.target === id && e.actor !== id,
  );
  const votes = tables(game)
    .filter((table) => until === undefined || table.closed <= until)
    .map((table) => ({
      day: table.day,
      voters: table.votes.filter((vote) => vote.target === id).map((vote) => vote.voter),
      of: table.votes.length,
      banished: table.banished === id,
    }))
    .filter((tally) => tally.voters.length > 0);
  const sightings: Sighting[] = [];
  for (const event of events) {
    if (event.actor !== id || !NOTABLE.has(event.type)) continue;
    for (const record of event.witnesses) {
      if (record.player === id || record.sense === "own") continue;
      const named = record.recorded_actor === id ? "them" : record.recorded_actor ? "someone else" : "nobody";
      sightings.push({ event, record, named });
    }
  }
  const secrets = events.filter((e) => e.actor === id && isSecret(e));
  const moments = game.feed.moments.filter(
    (moment) => moment.seats.includes(id) && (until === undefined || moment.first_seq <= until),
  );
  return { id, accusations, votes, sightings, secrets, moments };
}
