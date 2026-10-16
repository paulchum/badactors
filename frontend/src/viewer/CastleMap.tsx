// The castle drawn from above: each room at its light, cameras marked, and a
// token for every player where the feed puts them at the end of the tick,
// walking, asleep and dead included. Tokens wear their garment's colour, which
// is all a dim room would show of them.

import { useMemo } from "react";

import { type Plan, along, pathBetween, passagePath } from "../feed/layout";
import type { Game } from "../feed/load";
import { type NameStyle, seatLabel } from "../feed/naming";
import type { Token } from "../feed/positions";
import type { Lighting } from "../feed/types";

const GARMENTS: Record<string, string> = {
  "grey coat": "#a3abb5",
  "dark shawl": "#5b4777",
  "canvas jacket": "#cfa765",
};
const SPARE = ["#7fb7a4", "#c27c6b", "#8d9ad6", "#b9b06a"];

export function garmentColour(garment: string | null): string | null {
  if (garment === null) return null;
  if (garment in GARMENTS) return GARMENTS[garment];
  let hash = 0;
  for (const char of garment) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return SPARE[hash % SPARE.length];
}

const ROOM: Record<Lighting, { fill: string; text: string; edge: string }> = {
  bright: { fill: "#d8c597", text: "#2a1f0f", edge: "#f2dfae" },
  dim: { fill: "#5d5445", text: "#efe4cc", edge: "#7d725f" },
  dark: { fill: "#101114", text: "#8f949c", edge: "#2b2f36" },
};

/** Short labels for tokens: first letters, two where one would repeat. */
export function initials(game: Game): Map<string, string> {
  const first = (name: string, n: number) => name.slice(0, n);
  const out = new Map<string, string>();
  const seats = game.feed.seats;
  for (const seat of seats) {
    const one = first(seat.character, 1);
    const clash = seats.some((other) => other.id !== seat.id && first(other.character, 1) === one);
    out.set(seat.id, clash ? first(seat.character, 2) : one);
  }
  return out;
}

const R = 9;
const STEP = 21;
const FALLEN = 5.5;

/** Places for the living, in rows under the room's name. */
function slots(box: { x: number; y: number; w: number; h: number }, count: number): [number, number][] {
  const perRow = Math.max(1, Math.floor((box.w - 4) / STEP));
  const out: [number, number][] = [];
  for (let i = 0; i < count; i += 1) {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    out.push([box.x + 3 + STEP / 2 + col * STEP, box.y + 25 + row * STEP]);
  }
  return out;
}

/** Places for the dead, small, along the room's floor. */
function floor(box: { x: number; y: number; w: number; h: number }, count: number): [number, number][] {
  return Array.from({ length: count }, (_, i) => [box.x + 9 + i * 12.5, box.y + box.h - 8] as [number, number]);
}

/** What the map's marks mean. */
export function MapKey({ roles }: { roles: boolean }) {
  const swatch = (light: Lighting) => (
    <span className="inline-block h-3 w-4 rounded-sm align-[-2px]" style={{ background: ROOM[light].fill, border: `1px solid ${ROOM[light].edge}` }} />
  );
  return (
    <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-stone-400">
      <li>{swatch("bright")} bright: names everyone</li>
      <li>{swatch("dim")} dim: garments only</li>
      <li>{swatch("dark")} dark: shapes</li>
      <li>
        <span className="inline-block h-3 w-4 rounded-sm border border-dashed border-stone-400 align-[-2px]" /> private
      </li>
      <li>
        <span className="text-traitor">●</span> camera
      </li>
      <li>tokens wear their garment's colour</li>
      {roles ? (
        <li>
          <span className="text-traitor">●</span> on a token: traitor
        </li>
      ) : null}
      <li>z asleep · ✕ dead</li>
    </ul>
  );
}

export interface MapFocus {
  room?: string | null;
  actors?: string[];
}

export function CastleMap({
  game,
  plan,
  tokens,
  style,
  roles,
  focus,
}: {
  game: Game;
  plan: Plan;
  tokens: Token[];
  style: NameStyle;
  roles: boolean;
  focus: MapFocus;
}) {
  const labels = useMemo(() => initials(game), [game]);
  const placed = useMemo(() => {
    const byRoom = new Map<string, Token[]>();
    for (const token of tokens) {
      if (token.room === null) continue;
      const list = byRoom.get(token.room) ?? [];
      list.push(token);
      byRoom.set(token.room, list);
    }
    const at = new Map<string, [number, number]>();
    for (const [room, here] of byRoom) {
      const box = plan.rooms[room];
      if (!box) continue;
      const living = here.filter((t) => t.alive).sort((a, b) => a.seat - b.seat);
      const dead = here.filter((t) => !t.alive).sort((a, b) => a.seat - b.seat);
      slots(box, living.length).forEach((point, i) => at.set(living[i].id, point));
      floor(box, dead.length).forEach((point, i) => at.set(dead[i].id, point));
    }
    for (const token of tokens) {
      if (token.room !== null || !token.origin || !token.destination) continue;
      if (!plan.rooms[token.origin] || !plan.rooms[token.destination]) continue;
      at.set(token.id, along(pathBetween(plan, game.feed.passages, token.origin, token.destination), token.progress));
    }
    return at;
  }, [tokens, plan, game]);
  const actors = new Set(focus.actors ?? []);

  return (
    <svg
      viewBox={`0 0 ${plan.width} ${plan.height}`}
      className="h-auto w-full select-none"
      role="img"
      aria-label={`Map of ${game.feed.game.castle}`}
    >
      <defs>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2.4" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      {game.feed.passages.map((passage) => {
        const points = passagePath(plan, passage);
        if (points.some((p) => p === undefined)) return null;
        return (
          <polyline
            key={passage.rooms.join("|")}
            points={points.map((p) => p.join(",")).join(" ")}
            fill="none"
            stroke="#2b3138"
            strokeWidth={passage.travel_ticks > 1 ? 5 : 7}
            strokeDasharray={passage.travel_ticks > 1 ? "2 3" : undefined}
            strokeLinecap="round"
          />
        );
      })}
      {game.feed.rooms.map((room) => {
        const box = plan.rooms[room.id];
        if (!box) return null;
        const look = ROOM[room.lighting];
        const focused = focus.room === room.id;
        return (
          <g key={room.id}>
            <title>{`${room.name}: ${room.lighting}${room.private ? ", private" : ""}${room.camera ? ", watched by a camera" : ""}`}</title>
            <rect
              x={box.x}
              y={box.y}
              width={box.w}
              height={box.h}
              rx={5}
              fill={look.fill}
              stroke={focused ? "#f2c46d" : look.edge}
              strokeWidth={focused ? 2.5 : 1.2}
              strokeDasharray={room.private && !focused ? "4 3" : undefined}
            />
            <text x={box.x + 6} y={box.y + 12} fontSize={9} fontWeight={700} fill={look.text}>
              {room.name}
            </text>
            {room.camera ? (
              <g aria-label="camera" transform={`translate(${box.x + box.w - 18}, ${box.y + box.h - 12})`}>
                <title>Camera: logs every arrival and departure</title>
                <rect width={10} height={7} rx={1.5} fill="#1b1b1f" />
                <circle cx={4.2} cy={3.5} r={2} fill="#e13d45" />
                <polygon points="10,2 13,0.5 13,6.5 10,5" fill="#1b1b1f" />
              </g>
            ) : null}
          </g>
        );
      })}
      {tokens.map((token) => {
        const point = placed.get(token.id);
        if (!point) return null;
        const seat = game.seat(token.id)!;
        const fill = garmentColour(token.garment);
        const isFocus = actors.has(token.id);
        const status = !token.alive ? "dead" : token.asleep ? "asleep" : token.room === null ? "walking" : "";
        return (
          <g
            key={token.id}
            className="token"
            style={{ transform: `translate(${point[0]}px, ${point[1]}px)` }}
            opacity={!token.alive ? 0.55 : token.asleep ? 0.6 : 1}
          >
            <title>{`${seatLabel(seat, style)} · ${token.garment ?? "shirtsleeves"}${status ? ` · ${status}` : ""}`}</title>
            {isFocus ? <circle r={R + 3.5} fill="none" stroke="#f2c46d" strokeWidth={2} filter="url(#glow)" /> : null}
            <circle
              r={token.alive ? R : FALLEN}
              fill={fill ?? "#0d1014"}
              stroke={seat.played_by === "model" ? seat.colour : "#0b0d10"}
              strokeWidth={fill ? 1.6 : 1.2}
              strokeDasharray={fill ? undefined : "2 1.5"}
            />
            {token.alive ? <text
              y={3.3}
              fontSize={labels.get(token.id)!.length > 1 ? 7.5 : 9.5}
              fontWeight={800}
              textAnchor="middle"
              fill={token.garment === "dark shawl" || fill === null ? "#f5ecd9" : "#14161a"}
            >
              {labels.get(token.id)}
            </text> : null}
            {roles && token.traitor && token.alive ? <circle cx={R - 1} cy={-R + 1} r={3} fill="#e13d45" stroke="#0b0d10" strokeWidth={0.8} /> : null}
            {token.asleep && token.alive ? (
              <text x={R - 1} y={-R + 1} fontSize={7} fontWeight={800} fill="#c9d6ff">
                z
              </text>
            ) : null}
            {!token.alive ? (
              <path d={`M-3.5,-3.5 L3.5,3.5 M3.5,-3.5 L-3.5,3.5`} stroke="#e13d45" strokeWidth={1.6} />
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}
