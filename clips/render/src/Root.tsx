import React from "react";
import { Composition, registerRoot } from "remotion";

import { ClaimVsTruth } from "./ClaimVsTruth";
import { fixture } from "./fixture";
import type { ClipProps } from "./props";

// Props come from castle-clips; its beats set the clip's length.
export function RemotionRoot() {
  return (
    <Composition
      id="ClaimVsTruth"
      component={ClaimVsTruth}
      durationInFrames={fixture.duration}
      fps={fixture.fps}
      width={fixture.width}
      height={fixture.height}
      defaultProps={fixture}
      calculateMetadata={({ props }: { props: ClipProps }) => ({
        durationInFrames: props.duration,
        fps: props.fps,
        width: props.width,
        height: props.height,
      })}
    />
  );
}

registerRoot(RemotionRoot);
