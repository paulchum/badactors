import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";

type RenderEvent = {
  id: number;
  phase: string;
  type: string;
  payload: Record<string, unknown>;
};

export type MomentProps = {
  title: string;
  signal: string;
  reason: string;
  events: RenderEvent[];
};

const defaultMoment: MomentProps = {
  title: "Public certainty, private reversal",
  signal: "fixture",
  reason: "The public line lands first. The diary becomes the reveal.",
  events: [
    {
      id: 1,
      phase: "ROUND_TABLE",
      type: "PublicSpeech",
      payload: { player_id: "p2", speech: "I trust p4 completely." }
    },
    {
      id: 2,
      phase: "ROUND_TABLE",
      type: "DiaryEntry",
      payload: { player_id: "p2", diary: "I am setting p4 up for the vote." }
    }
  ]
};

export function BadActorsClip(props: Partial<MomentProps>) {
  const moment = { ...defaultMoment, ...props };
  const frame = useCurrentFrame();
  const diaryOpacity = interpolate(frame, [70, 95], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp"
  });
  const publicLine = lineForType(moment.events, "PublicSpeech", "speech");
  const diaryLine = lineForType(moment.events, "DiaryEntry", "diary");

  return (
    <AbsoluteFill
      style={{
        background: "#080d11",
        color: "#fff7ed",
        fontFamily:
          "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
        padding: 56
      }}
    >
      <div style={{ color: "#e13d45", fontSize: 34, fontWeight: 900 }}>
        BAD ACTORS
      </div>
      <h1 style={{ fontSize: 58, lineHeight: 1, margin: "48px 0 28px" }}>
        {moment.title}
      </h1>
      <section
        style={{
          border: "2px solid #2c7be5",
          borderRadius: 8,
          padding: 28,
          marginBottom: 28
        }}
      >
        <div style={{ color: "#76aef4", fontSize: 22, fontWeight: 900 }}>
          PUBLIC
        </div>
        <p style={{ fontSize: 40, lineHeight: 1.18, margin: "16px 0 0" }}>
          {publicLine}
        </p>
      </section>
      <section
        style={{
          border: "2px solid #e13d45",
          borderRadius: 8,
          padding: 28,
          opacity: diaryOpacity
        }}
      >
        <div style={{ color: "#ff7b83", fontSize: 22, fontWeight: 900 }}>
          DIARY
        </div>
        <p style={{ fontSize: 42, lineHeight: 1.18, margin: "16px 0 0" }}>
          {diaryLine}
        </p>
      </section>
      <div style={{ marginTop: 38, color: "#d89b1d", fontSize: 26 }}>
        {moment.reason}
      </div>
      <div style={{ bottom: 48, color: "#94a3b8", left: 56, position: "absolute" }}>
        {moment.signal} · events {moment.events.map((event) => event.id).join(", ")}
      </div>
    </AbsoluteFill>
  );
}

function lineForType(events: RenderEvent[], type: string, payloadKey: string) {
  const event = events.find((candidate) => candidate.type === type);
  const value = event?.payload[payloadKey];
  if (typeof value === "string") {
    return value;
  }
  return "The moment turns on what was said and what was hidden.";
}
