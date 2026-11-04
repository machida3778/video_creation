import React from 'react';
import {AbsoluteFill, Audio, Sequence, staticFile, useVideoConfig} from 'remotion';
import {CaptionView} from './components/Caption';
import {ClipView} from './components/registry';
import type {ShortProps} from './types';

const dbToGain = (db: number) => Math.pow(10, db / 20);

export const Short: React.FC<ShortProps> = ({timeline}) => {
  const {fps} = useVideoConfig();
  const frame = (sec: number) => Math.round(sec * fps);
  const span = (start: number, end: number) => Math.max(1, frame(end) - frame(start));
  const voice = timeline.audio.filter((a) => a.kind === 'voice');
  // Voice intervals in frames, for ducking music under narration (end = next voice start or +6 s).
  const voiceSpans = voice.map((a, i) => [frame(a.start), voice[i + 1] ? frame(voice[i + 1].start) : frame(a.start + 6)]);
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
      {timeline.audio.map((a, i) => {
        const gain = dbToGain(a.gain_db);
        const start = frame(a.start);
        return (
          <Sequence key={`a${i}`} from={start} layout="none">
            <Audio
              src={staticFile(a.src)}
              volume={(f) =>
                a.duck_under_voice && voiceSpans.some(([s, e]) => f + start >= s && f + start < e) ? gain * 0.35 : gain
              }
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
