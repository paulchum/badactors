import React from "react";
import { Composition, registerRoot } from "remotion";

import { BadActorsClip, type MomentProps } from "./Clip";

export function RemotionRoot() {
  return (
    <Composition
      id="BadActorsClip"
      component={BadActorsClip}
      durationInFrames={180}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={{} satisfies Partial<MomentProps>}
    />
  );
}

registerRoot(RemotionRoot);
