import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Clip} from '../types';
import {CaptionLines, FONT} from './Caption';

type P = Record<string, unknown>;
const str = (v: unknown, d = '') => (typeof v === 'string' ? v : d);
const num = (v: unknown, d: number) => (typeof v === 'number' ? v : d);

// Background: solid colour or vertical gradient.
const Background: React.FC<{props: P}> = ({props}) => (
  <AbsoluteFill
    style={{background: `linear-gradient(180deg, ${str(props.from, '#1b2a41')} 0%, ${str(props.to, str(props.from, '#0f1419'))} 100%)`}}
  />
);

// Framings cycled by `cut_sec`: each cut jumps to a new zoom/pan so the picture changes every few seconds
// without a new image (retention: a visual change every 2-3 s).
const CUTS: {zoom: [number, number]; pan: string}[] = [
  {zoom: [1.0, 1.1], pan: 'none'},
  {zoom: [1.28, 1.2], pan: 'left'},
  {zoom: [1.12, 1.22], pan: 'up'},
  {zoom: [1.35, 1.28], pan: 'right'},
];

// Still image with a slow zoom/pan over the clip ("Ken Burns"); with props.cut_sec it also cuts between framings.
const KenBurnsImage: React.FC<{props: P; frames: number}> = ({props, frames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const cutFrames = typeof props.cut_sec === 'number' && props.cut_sec > 0 ? Math.round(props.cut_sec * fps) : 0;
  const k = cutFrames ? Math.floor(frame / cutFrames) : 0;
  const local = cutFrames ? frame - k * cutFrames : frame;
  const span = cutFrames ? Math.min(cutFrames, frames - k * cutFrames) : frames;
  const t = interpolate(local, [0, Math.max(1, span - 1)], [0, 1], {extrapolateRight: 'clamp'});
  const base = Array.isArray(props.zoom) ? (props.zoom as [number, number]) : [1.0, 1.12];
  const cut = cutFrames ? CUTS[(k + num(props.cut_offset, 0)) % CUTS.length] : {zoom: base, pan: str(props.pan, 'none')};
  const [z0, z1] = k === 0 && !cutFrames ? base : cut.zoom;
  const pan = cut.pan;
  const d = 40 * t;
  const shift = {left: [-d, 0], right: [d, 0], up: [0, -d], down: [0, d], none: [0, 0]}[pan] ?? [0, 0];
  return (
    <AbsoluteFill style={{overflow: 'hidden'}}>
      <Img
        src={staticFile(str(props.src))}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          transform: `scale(${z0 + (z1 - z0) * t}) translate(${shift[0]}px, ${shift[1]}px)`,
        }}
      />
    </AbsoluteFill>
  );
};

const VideoClip: React.FC<{props: P}> = ({props}) => (
  <AbsoluteFill>
    <OffthreadVideo
      src={staticFile(str(props.src))}
      muted={props.muted !== false}
      style={{width: '100%', height: '100%', objectFit: 'cover'}}
    />
  </AbsoluteFill>
);

// Big title in the upper half (hook / title card). `text` may contain '\n' (BudouX breaks from vg).
const TitleCard: React.FC<{props: P}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({frame, fps, config: {damping: 12, stiffness: 180}, durationInFrames: 12});
  return (
    <AbsoluteFill>
      {props.background !== false && <Background props={props} />}
      <AbsoluteFill style={{transform: `scale(${0.85 + 0.15 * pop})`, opacity: pop, transformOrigin: '50% 30%'}}>
        <CaptionLines text={str(props.text)} emphasis={(props.emphasis as string[]) ?? []} style="title" centerY={num(props.y, 560)} />
      </AbsoluteFill>
      {str(props.sub) && (
        <div style={{position: 'absolute', top: num(props.y, 560) + 190, left: 120, width: 780, textAlign: 'center',
          fontFamily: FONT, fontWeight: 700, fontSize: 40, color: '#e8edf2', whiteSpace: 'pre-line'}}>
          {str(props.sub)}
        </div>
      )}
    </AbsoluteFill>
  );
};

// Small label near the top of the safe area, e.g. 「出典: 総務省 統計局」.
const SourceCard: React.FC<{props: P}> = ({props}) => (
  <div style={{position: 'absolute', top: 250, left: 120, maxWidth: 780, padding: '10px 22px', borderRadius: 14,
    background: 'rgba(0,0,0,0.55)', color: '#f5f7fa', fontFamily: FONT, fontWeight: 700, fontSize: 30, lineHeight: 1.3}}>
    {str(props.text)}
  </div>
);

const Placeholder: React.FC<{name: string}> = ({name}) => (
  <AbsoluteFill style={{background: '#5a0040', color: '#fff', fontFamily: FONT, fontSize: 48, justifyContent: 'center', alignItems: 'center'}}>
    UNKNOWN COMPONENT: {name}
  </AbsoluteFill>
);

export const COMPONENTS = ['background', 'ken_burns_image', 'video_clip', 'title_card', 'hook_title', 'end_card', 'source_card'];

export const ClipView: React.FC<{clip: Clip; frames: number}> = ({clip, frames}) => {
  const p = clip.props ?? {};
  switch (clip.component) {
    case 'background':
      return <Background props={p} />;
    case 'ken_burns_image':
      return <KenBurnsImage props={p} frames={frames} />;
    case 'video_clip':
      return <VideoClip props={p} />;
    case 'title_card':
    case 'hook_title':
    case 'end_card':
      return <TitleCard props={p} />;
    case 'source_card':
      return <SourceCard props={p} />;
    default:
      return <Placeholder name={clip.component} />;
  }
};
