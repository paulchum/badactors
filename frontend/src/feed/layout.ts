// Where each room is drawn. The feed carries rooms and passages but no floor
// plan, so the plan of each castle is authored here; a castle with none gets a
// plain grid.

import type { FeedPassage, FeedRoom } from "./types";

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Plan {
  width: number;
  height: number;
  rooms: Record<string, Box>;
  /** Bends for passages a straight line would draw through another room,
   * keyed "a|b" in the passage's order. */
  bends: Record<string, [number, number][]>;
}

const GREYWATER: Plan = {
  width: 400,
  height: 300,
  rooms: {
    library: { x: 8, y: 8, w: 84, h: 62 },
    gallery: { x: 108, y: 8, w: 116, h: 62 },
    armoury: { x: 240, y: 8, w: 76, h: 62 },
    chambers: { x: 8, y: 86, w: 84, h: 96 },
    great_hall: { x: 108, y: 86, w: 116, h: 96 },
    study: { x: 240, y: 86, w: 76, h: 46 },
    dining_room: { x: 240, y: 144, w: 76, h: 62 },
    kitchen: { x: 328, y: 144, w: 64, h: 62 },
    chapel: { x: 108, y: 198, w: 116, h: 54 },
    crypt: { x: 8, y: 198, w: 84, h: 94 },
    cellar: { x: 328, y: 222, w: 64, h: 70 },
  },
  bends: {
    "cellar|crypt": [
      [360, 280],
      [50, 280],
    ],
  },
};

const PLANS: Record<string, Plan> = { greywater: GREYWATER };

function gridPlan(rooms: FeedRoom[]): Plan {
  const columns = Math.ceil(Math.sqrt(rooms.length));
  const plan: Plan = { width: columns * 110, height: Math.ceil(rooms.length / columns) * 90, rooms: {}, bends: {} };
  rooms.forEach((room, index) => {
    plan.rooms[room.id] = { x: (index % columns) * 110 + 8, y: Math.floor(index / columns) * 90 + 8, w: 94, h: 74 };
  });
  return plan;
}

export function planFor(castleId: string, rooms: FeedRoom[]): Plan {
  const plan = PLANS[castleId];
  if (plan && rooms.every((room) => room.id in plan.rooms)) return plan;
  return gridPlan(rooms);
}

export function centre(box: Box): [number, number] {
  return [box.x + box.w / 2, box.y + box.h / 2];
}

/** The points a passage is drawn through, from its first room to its second. */
export function passagePath(plan: Plan, passage: FeedPassage): [number, number][] {
  const [a, b] = passage.rooms;
  const start = centre(plan.rooms[a]);
  const end = centre(plan.rooms[b]);
  const bends = plan.bends[`${a}|${b}`] ?? plan.bends[`${b}|${a}`]?.slice().reverse() ?? [];
  return [start, ...bends, end];
}

/** A point part of the way along a path, by length. */
export function along(path: [number, number][], share: number): [number, number] {
  const lengths = path.slice(1).map((point, i) => Math.hypot(point[0] - path[i][0], point[1] - path[i][1]));
  let left = lengths.reduce((sum, length) => sum + length, 0) * share;
  for (let i = 0; i < lengths.length; i += 1) {
    if (left <= lengths[i] || i === lengths.length - 1) {
      const t = lengths[i] === 0 ? 0 : Math.min(left / lengths[i], 1);
      return [path[i][0] + (path[i + 1][0] - path[i][0]) * t, path[i][1] + (path[i + 1][1] - path[i][1]) * t];
    }
    left -= lengths[i];
  }
  return path[path.length - 1];
}

/** The path between two rooms, oriented from one to the other. */
export function pathBetween(plan: Plan, passages: FeedPassage[], from: string, to: string): [number, number][] {
  const passage = passages.find((p) => p.rooms.includes(from) && p.rooms.includes(to));
  if (!passage) return [centre(plan.rooms[from]), centre(plan.rooms[to])];
  const path = passagePath(plan, passage);
  return passage.rooms[0] === from ? path : path.slice().reverse();
}
