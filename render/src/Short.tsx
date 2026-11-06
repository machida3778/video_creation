import React from 'react';
import {AbsoluteFill, Sequence, useVideoConfig} from 'remotion';
import {CaptionView} from './components/Caption';
import {ClipView} from './components/registry';
import type {ShortProps} from './types';

export const Short: React.FC<ShortProps> = ({timeline}) => {
  const {fps} = useVideoConfig();
  const frame = (sec: number) => Math.round(sec * fps);
  const span = (start: number, end: number) => Math.max(1, frame(end) - frame(start));
  const clips = [...timeline.clips].sort((a, b) => a.layer - b.layer);

  return (
    <AbsoluteFill style={{backgroundColor: '#0f1419'}}>
      {clips.map((clip, i) => (
        <Sequence key={`c${i}`} from={frame(clip.start)} durationInFrames={span(clip.start, clip.end)} layout="none">
          <ClipView clip={clip} frames={span(clip.start, clip.end)} />
        </Sequence>
      ))}
      {timeline.captions.map((cap, i) => (
        <Sequence key={`t${i}`} from={frame(cap.start)} durationInFrames={span(cap.start, cap.end)} layout="none">
          <CaptionView caption={cap} />
        </Sequence>
      ))}
      {/* Audio is mixed by vg.mix for both Japanese rendering and English dubbing. */}
    </AbsoluteFill>
  );
};
