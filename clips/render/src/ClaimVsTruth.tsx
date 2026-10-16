import React from "react";
import {
  AbsoluteFill,
  Easing,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

import type {
  ClaimBeat,
  ClipProps,
  Figure,
  Lighting,
  LineBeat,
  Receipt,
  Stamp,
  TruthBeat,
} from "./props";
import { schedule } from "./props";

// One beat at a time, never parallel lanes (SPEC section 8): the claim, the
// rewind to what the ledger shows, the speaker's own private line, the stamp,
// and the receipt. Every motion is a function of the frame number.

const INK = "#f4ede0";
const MUTED = "#a59d90";
const NIGHT = "#0b0d10";
const RED = "#e0343c";
const SANS =
  '"Helvetica Neue", Helvetica, Arial, "Segoe UI", ui-sans-serif, sans-serif';
const SERIF = 'Georgia, "Times New Roman", ui-serif, serif';
const SIDE = 72;

const GARMENTS: Record<string, string> = {
  "grey coat": "#8e949c",
  "dark shawl": "#5a4d6e",
  "canvas jacket": "#a8915f",
};
const SHIRTSLEEVES = "#cfc6b4";

export function ClaimVsTruth(props: ClipProps) {
  const at = schedule(props.beats);
  return (
    <AbsoluteFill style={{ background: NIGHT, color: INK, fontFamily: SANS }}>
      <Sequence from={at.claim.from} durationInFrames={at.claim.frames}>
        <ClaimScene claim={props.claim} frames={at.claim.frames} />
      </Sequence>
      <Sequence from={at.rewind.from} durationInFrames={at.rewind.frames}>
        <RewindScene truth={props.truth} from={props.claim.tick} />
      </Sequence>
      <Sequence from={at.line.from} durationInFrames={at.line.frames}>
        <LineScene line={props.line} />
      </Sequence>
      <Sequence from={at.stamp.from} durationInFrames={at.stamp.frames}>
        <StampScene stamp={props.stamp} />
      </Sequence>
      <Sequence from={at.receipt.from} durationInFrames={at.receipt.frames}>
        <ReceiptScene receipt={props.receipt} />
      </Sequence>
      <Chrome banner={props.banner} beats={props.beats.map((b) => b.name)} at={at} />
    </AbsoluteFill>
  );
}

// ------------------------------------------------------------------ chrome

function Chrome({
  banner,
  beats,
  at,
}: {
  banner: string | null;
  beats: string[];
  at: ReturnType<typeof schedule>;
}) {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div
        style={{
          position: "absolute",
          top: 64,
          left: SIDE,
          right: SIDE,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div style={{ color: RED, fontSize: 34, fontWeight: 900, letterSpacing: 6 }}>
          BAD ACTORS
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          {beats.map((name) => {
            const slot = at[name as keyof typeof at];
            const lit = frame >= slot.from;
            return (
              <div
                key={name}
                style={{
                  width: 44,
                  height: 8,
                  borderRadius: 4,
                  background: lit ? INK : "#3a3d42",
                }}
              />
            );
          })}
        </div>
      </div>
      {banner ? (
        <div
          style={{
            position: "absolute",
            top: 124,
            left: SIDE,
            right: SIDE,
            padding: "14px 16px",
            border: "2px solid #c9a227",
            color: "#e8c547",
            fontSize: 22,
            fontWeight: 700,
            whiteSpace: "nowrap",
            borderRadius: 8,
            textAlign: "center",
          }}
        >
          {banner}
        </div>
      ) : null}
    </AbsoluteFill>
  );
}

/** Burned-in captions: the beat's words, typed out as the beat runs. */
function Caption({ text, delay = 0 }: { text: string; delay?: number }) {
  const frame = useCurrentFrame();
  const shown = Math.max(0, Math.floor((frame - delay) * 1.5));
  return (
    <div
      style={{
        position: "absolute",
        left: SIDE - 16,
        right: SIDE - 16,
        bottom: 120,
        minHeight: 160,
        padding: "28px 32px",
        background: "rgba(0, 0, 0, 0.78)",
        borderRadius: 12,
        fontSize: captionSize(text),
        lineHeight: 1.25,
        fontWeight: 600,
        color: "#ffffff",
      }}
    >
      {text.slice(0, shown)}
    </div>
  );
}

function captionSize(text: string): number {
  if (text.length <= 90) return 44;
  if (text.length <= 180) return 38;
  return 32;
}

function quoteSize(text: string): number {
  if (text.length <= 60) return 76;
  if (text.length <= 120) return 64;
  if (text.length <= 200) return 54;
  return 44;
}

function fadeIn(frame: number, start = 0, length = 12): number {
  return interpolate(frame, [start, start + length], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
}

function Kicker({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        color: MUTED,
        fontSize: 30,
        fontWeight: 700,
        letterSpacing: 4,
        textTransform: "uppercase",
      }}
    >
      {children}
    </div>
  );
}

function Chip({ name, colour }: { name: string; colour: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
      <div style={{ width: 34, height: 34, borderRadius: 17, background: colour }} />
      <div style={{ fontSize: 46, fontWeight: 800 }}>{name}</div>
    </div>
  );
}

// ------------------------------------------------------------- the claim

function ClaimScene({ claim, frames }: { claim: ClaimBeat; frames: number }) {
  const frame = useCurrentFrame();
  const rise = interpolate(frame, [0, 18], [40, 0], {
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  const leave = interpolate(frame, [frames - 10, frames], [1, 0], {
    extrapolateLeft: "clamp",
  });
  return (
    <AbsoluteFill style={{ opacity: leave }}>
      <div
        style={{
          position: "absolute",
          top: 260,
          left: SIDE,
          right: SIDE,
          opacity: fadeIn(frame),
          transform: `translateY(${rise}px)`,
        }}
      >
        <Kicker>
          Day {claim.day} · {claim.phase} · {claim.room}
        </Kicker>
        <div style={{ marginTop: 36 }}>
          <Chip name={claim.speaker.name} colour={claim.speaker.colour} />
        </div>
        <div style={{ marginTop: 18, color: MUTED, fontSize: 32 }}>{claim.said}</div>
        <div
          style={{
            marginTop: 48,
            padding: "48px 44px",
            borderLeft: `10px solid ${claim.speaker.colour}`,
            background: "#15181d",
            borderRadius: 10,
            fontFamily: SERIF,
            fontSize: quoteSize(claim.words),
            lineHeight: 1.18,
          }}
        >
          “{claim.words}”
        </div>
        <div style={{ marginTop: 28, color: MUTED, fontSize: 28 }}>
          What they said, at tick {claim.tick}.
        </div>
      </div>
      <Caption text={`“${claim.words}”`} delay={10} />
    </AbsoluteFill>
  );
}

// ------------------------------------------------------------ the rewind

const REWIND = 36;

function RewindScene({ truth, from }: { truth: TruthBeat; from: number }) {
  const frame = useCurrentFrame();
  const tick = Math.round(
    interpolate(frame, [0, REWIND - 6], [from, truth.tick], {
      extrapolateRight: "clamp",
      easing: Easing.inOut(Easing.quad),
    }),
  );
  const sweep = interpolate(frame, [0, REWIND], [1, 0], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill>
      {frame < REWIND ? (
        <AbsoluteFill
          style={{
            justifyContent: "center",
            alignItems: "center",
            background: `repeating-linear-gradient(0deg, rgba(255,255,255,${
              0.06 * sweep
            }) 0px, rgba(255,255,255,${0.06 * sweep}) 3px, transparent 3px, transparent ${
              14 + ((frame * 7) % 14)
            }px)`,
          }}
        >
          <div style={{ fontSize: 120, fontWeight: 900, letterSpacing: 8 }}>◀◀</div>
          <div style={{ marginTop: 24, fontSize: 44, fontWeight: 800, letterSpacing: 10 }}>
            REWIND
          </div>
          <div style={{ marginTop: 28, fontSize: 64, fontFamily: "monospace" }}>
            tick {tick}
          </div>
        </AbsoluteFill>
      ) : (
        <Sequence from={REWIND}>
          <TruthRoom truth={truth} />
        </Sequence>
      )}
    </AbsoluteFill>
  );
}

const LIGHT: Record<Lighting, { floor: string; wall: string; note: string }> = {
  bright: { floor: "#d9cdb2", wall: "#efe5cf", note: "bright · everyone named" },
  dim: { floor: "#3d372c", wall: "#4b4436", note: "dim · garments only" },
  dark: { floor: "#08090b", wall: "#0d0f12", note: "dark · shapes only" },
};

function TruthRoom({ truth }: { truth: TruthBeat }) {
  const frame = useCurrentFrame();
  const light = LIGHT[truth.light];
  const rows = chunk(truth.figures, 5);
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", top: 230, left: SIDE, right: SIDE }}>
        <Kicker>
          The ledger · Day {truth.day} · {truth.phase} · tick {truth.tick}
        </Kicker>
        <div style={{ marginTop: 18, display: "flex", alignItems: "baseline", gap: 20 }}>
          <div style={{ fontSize: 58, fontWeight: 800 }}>{truth.room}</div>
          <div style={{ fontSize: 28, color: MUTED }}>{light.note}</div>
        </div>
        <div
          style={{
            marginTop: 26,
            height: 640,
            borderRadius: 14,
            background: `linear-gradient(180deg, ${light.wall} 0%, ${light.floor} 100%)`,
            border: "2px solid #2a2d33",
            position: "relative",
            overflow: "hidden",
            opacity: fadeIn(frame, 0, 10),
          }}
        >
          {truth.light === "dim" ? (
            <div
              style={{
                position: "absolute",
                inset: 0,
                background:
                  "radial-gradient(circle at 50% 18%, rgba(255,214,140,0.35), rgba(0,0,0,0.55) 70%)",
              }}
            />
          ) : null}
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              gap: 26,
              padding: "20px 12px",
            }}
          >
            {rows.map((row, r) => (
              <div key={r} style={{ display: "flex", justifyContent: "center", gap: 14 }}>
                {row.map((figure, i) => (
                  <Person
                    key={`${r}-${i}`}
                    figure={figure}
                    light={truth.light}
                    appear={fadeIn(frame, 4 + (r * 5 + i) * 2, 8)}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
        {truth.words ? (
          <div
            style={{
              marginTop: 22,
              fontFamily: SERIF,
              fontSize: 32,
              lineHeight: 1.25,
              color: INK,
              opacity: fadeIn(frame, 20),
            }}
          >
            “{truth.words}”
          </div>
        ) : null}
        <div style={{ marginTop: 22, display: "flex", flexDirection: "column", gap: 12 }}>
          {truth.witnessed.map((w, i) => (
            <div
              key={i}
              style={{
                opacity: fadeIn(frame, 30 + i * 18),
                fontSize: 27,
                lineHeight: 1.25,
                color: "#d8d1c4",
              }}
            >
              <span style={{ fontWeight: 800 }}>{w.by}</span>
              <span style={{ color: MUTED }}> wrote down ({w.confidence}): </span>
              <span style={{ fontFamily: SERIF, fontStyle: "italic" }}>{w.text}</span>
            </div>
          ))}
        </div>
      </div>
      <Caption
        text={`The ledger: ${truth.text}${truth.words ? ` “${truth.words}”` : ""}`}
        delay={6}
      />
    </AbsoluteFill>
  );
}

function Person({
  figure,
  light,
  appear,
}: {
  figure: Figure;
  light: Lighting;
  appear: number;
}) {
  const frame = useCurrentFrame();
  const fill =
    figure.look === "named"
      ? figure.colour ?? SHIRTSLEEVES
      : figure.look === "garment"
        ? (figure.garment && GARMENTS[figure.garment]) || SHIRTSLEEVES
        : "#16181c";
  const head = figure.look === "named" ? "#e6c9a8" : figure.look === "garment" ? "#6b6152" : "#16181c";
  const outline = light === "dark" ? "#2b2f36" : "rgba(0,0,0,0.35)";
  const pulse = figure.actor ? 0.55 + 0.45 * Math.sin((frame / 30) * Math.PI * 2) : 0;
  const text = light === "bright" ? "#1d1a16" : light === "dim" ? "#e9e0cf" : "#6f747c";
  return (
    <div style={{ width: 172, textAlign: "center", opacity: appear }}>
      <svg
        width={120}
        height={figure.asleep ? 110 : 200}
        viewBox={figure.asleep ? "0 0 200 110" : "0 0 120 200"}
        style={{ overflow: "visible" }}
      >
        <g transform={figure.asleep ? "translate(200, 10) rotate(90)" : undefined}>
          {figure.actor ? (
            <ellipse
              cx={60}
              cy={110}
              rx={64}
              ry={104}
              fill="none"
              stroke={RED}
              strokeWidth={6}
              opacity={pulse}
            />
          ) : null}
          <circle cx={60} cy={38} r={28} fill={head} stroke={outline} strokeWidth={3} />
          <path
            d="M20 196 L28 92 Q60 70 92 92 L100 196 Z"
            fill={fill}
            stroke={outline}
            strokeWidth={3}
          />
        </g>
      </svg>
      <div style={{ marginTop: 8, fontSize: 22, lineHeight: 1.15, color: text, fontWeight: 700 }}>
        {figure.label}
      </div>
      {figure.look === "named" && figure.garment ? (
        <div style={{ fontSize: 19, color: text, opacity: 0.75 }}>{figure.garment}</div>
      ) : null}
    </div>
  );
}

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

// --------------------------------------------------------------- the line

function LineScene({ line }: { line: LineBeat }) {
  const frame = useCurrentFrame();
  const shown = Math.max(0, Math.floor((frame - 14) * 1.5));
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", top: 300, left: SIDE, right: SIDE, opacity: fadeIn(frame) }}>
        <Kicker>In their own words</Kicker>
        <div style={{ marginTop: 30 }}>
          <Chip name={line.speaker.name} colour={line.speaker.colour} />
        </div>
        <div style={{ marginTop: 14, color: MUTED, fontSize: 30 }}>{line.kind}</div>
        <div
          style={{
            marginTop: 40,
            padding: "56px 48px",
            background: "#efe6d2",
            color: "#2a2420",
            borderRadius: 6,
            boxShadow: "0 30px 80px rgba(0,0,0,0.5)",
            transform: "rotate(-1.2deg)",
            fontFamily: SERIF,
            fontStyle: "italic",
            fontSize: quoteSize(line.text),
            lineHeight: 1.25,
            minHeight: 260,
          }}
        >
          {line.text.slice(0, shown)}
        </div>
        <div style={{ marginTop: 26, color: MUTED, fontSize: 26 }}>
          Only the audience reads this. Nobody in the castle ever did.
        </div>
      </div>
      <Caption text={`“${line.text}”`} delay={14} />
    </AbsoluteFill>
  );
}

// -------------------------------------------------------------- the stamp

function StampScene({ stamp }: { stamp: Stamp }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const hit = spring({ frame, fps, config: { damping: 11, stiffness: 160, mass: 0.7 } });
  const scale = interpolate(hit, [0, 1], [2.4, 1]);
  const size = stamp.label.length <= 8 ? 150 : stamp.label.length <= 12 ? 112 : 84;
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          transform: `rotate(-9deg) scale(${scale})`,
          opacity: interpolate(frame, [0, 4], [0, 1], { extrapolateRight: "clamp" }),
          border: `12px solid ${RED}`,
          borderRadius: 18,
          padding: "26px 44px",
          color: RED,
          fontSize: size,
          fontWeight: 900,
          letterSpacing: 6,
          textAlign: "center",
          maxWidth: 940,
          lineHeight: 1,
        }}
      >
        {stamp.label}
      </div>
      <div
        style={{
          marginTop: 70,
          color: MUTED,
          fontSize: 30,
          opacity: fadeIn(frame, 16),
          textAlign: "center",
          padding: `0 ${SIDE}px`,
        }}
      >
        {stamp.note}
      </div>
      <Caption text={stamp.headline} delay={12} />
    </AbsoluteFill>
  );
}

// ------------------------------------------------------------ the receipt

function ReceiptScene({ receipt }: { receipt: Receipt }) {
  const frame = useCurrentFrame();
  const rows: [string, string][] = [
    ["Castle", receipt.castle],
    ["Game", receipt.world_id],
    ["Seed", String(receipt.seed)],
    ["Day", String(receipt.day)],
    ["Ticks", `${receipt.first_tick} to ${receipt.last_tick}`],
    ["Ledger", `entries ${receipt.first_seq} to ${receipt.last_seq}`],
    ["Moment", receipt.moment],
    ["Played by", receipt.reference_bots ? "Reference bots, no model" : "Models"],
  ];
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          width: 1080 - SIDE * 2,
          padding: "56px 52px",
          background: "#f2ecdf",
          color: "#1d1a16",
          borderRadius: 8,
          fontFamily: "ui-monospace, Menlo, monospace",
          opacity: fadeIn(frame),
          transform: `translateY(${interpolate(frame, [0, 14], [60, 0], {
            extrapolateRight: "clamp",
          })}px)`,
        }}
      >
        <div style={{ fontSize: 44, fontWeight: 800, letterSpacing: 8, textAlign: "center" }}>
          RECEIPT
        </div>
        <div style={{ borderTop: "3px dashed #8c8476", margin: "28px 0" }} />
        {rows.map(([key, value]) => (
          <div
            key={key}
            style={{ display: "flex", justifyContent: "space-between", fontSize: 32, margin: "14px 0", gap: 24 }}
          >
            <span style={{ color: "#6b6357" }}>{key}</span>
            <span style={{ fontWeight: 700, textAlign: "right", wordBreak: "break-all" }}>{value}</span>
          </div>
        ))}
        <div style={{ borderTop: "3px dashed #8c8476", margin: "28px 0" }} />
        <div style={{ fontSize: 26, textAlign: "center", color: "#4d473e" }}>
          Every frame replays from this game's record.
        </div>
      </div>
    </AbsoluteFill>
  );
}
