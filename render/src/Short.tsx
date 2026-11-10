import React from 'react';
import {AbsoluteFill, Easing, interpolate, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {CaptionView, FONT, LayoutContext} from './components/Caption';
import {ClipView} from './components/registry';
import {Bokeh, FirstBuildUp, FirstBurst, LightSweep, revealSec, Sparks, tierColor} from './components/Montage';
import type {Clip, Layout, ShortProps, TextBand} from './types';

// Base-layer components that show the scene's picture; a format's media frame places them.
const MEDIA = new Set(['ken_burns_image', 'video_clip']);
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// Only base media is wrapped: labels and existing components retain their own animation.
// Countdown pictures never stand still: each arrives a little close and settles, keeps drifting sideways
// (alternating direction) while slowly pushing in, catches one glint of light, and has soft lights floating
// over it. The 1st place (tier top1) first builds up on a dark, blurred picture, then flashes into view.
const MontageMedia: React.FC<{clip: Clip; frames: number; children: React.ReactNode}> = ({clip, frames, children}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = clip.props;
  const t = frame / fps;
  const sceneSec = frames / fps;
  const rank = Number(p.rank);
  const ranked = Number.isFinite(rank) && rank > 0;
  const top = ['top5', 'top3', 'top1'].includes(String(p.tier));
  const first = p.tier === 'top1';
  const build = first ? revealSec(p.reveal, sceneSec) : 0;
  const after = t - build;  // seconds since the picture became visible
  const mirror = p.transition === 'mirror' && frame < fps * 0.3;
  const shift = mirror ? interpolate(frame, [0, fps * 0.3], [680, 0]) : 0;
  const split = first ? interpolate(after, [0, 0.5], [14, 5], clamp) : 3;
  const color = typeof p.color === 'string' ? p.color : ranked && top ? tierColor(rank) : '#ffffff';
  // Scale stays >= 1.05 so the +-18 px drift never shows an edge.
  const progress = interpolate(t, [0, Math.max(0.1, sceneSec)], [0, 1], clamp);
  const settle = after < 0
    ? interpolate(t, [0, build], [1.25, 1.18], clamp)
    : interpolate(after, [0, first ? 0.9 : 0.7], [first ? 1.22 : 1.14, 1.05],
      {...clamp, easing: Easing.out(Easing.cubic)});
  const scale = settle + 0.05 * progress;
  const direction = ranked && rank % 2 === 0 ? -1 : 1;
  const drift = interpolate(progress, [0, 1], [-18, 18]) * direction;
  const hidden = after < 0 ? interpolate(t, [0, build], [0, 1], clamp) : null;
  const filter = hidden === null ? undefined
    : `brightness(${0.25 + 0.15 * hidden}) blur(${22 - 8 * hidden}px) saturate(0.6)`;
  const picture = <AbsoluteFill style={{transform: `translateX(${drift}px) scale(${scale})`, filter}}>
    {children}
    {top && <>
      <AbsoluteFill style={{transform: `translateX(-${split}px)`, mixBlendMode: 'screen', opacity: 0.23,
        filter: 'url(#montage-red)'}}>{children}</AbsoluteFill>
      <AbsoluteFill style={{transform: `translateX(${split}px)`, mixBlendMode: 'screen', opacity: 0.23,
        filter: 'url(#montage-blue)'}}>{children}</AbsoluteFill>
    </>}
  </AbsoluteFill>;
  return <AbsoluteFill style={{overflow: 'hidden'}}>
    {top && <svg width="0" height="0" style={{position: 'absolute'}} aria-hidden="true"><defs>
      <filter id="montage-red" colorInterpolationFilters="sRGB"><feColorMatrix type="matrix"
        values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" /></filter>
      <filter id="montage-blue" colorInterpolationFilters="sRGB"><feColorMatrix type="matrix"
        values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" /></filter>
    </defs></svg>}
    {mirror ? [-1, 0, 1].map(tile => <AbsoluteFill key={tile}
      style={{transform: `translateX(${tile * 1080 + shift}px) scaleX(${tile === 0 ? 1 : -1})`}}>
      {picture}
    </AbsoluteFill>) : picture}
    {after >= 0 && <>
      <Bokeh color={color} />
      <LightSweep start={build + 0.12} strength={first ? 0.4 : 0.28} />
      {top && ranked && rank <= 3 && <Sparks rank={rank} color={typeof p.color === 'string' ? p.color : undefined} />}
    </>}
    {first && <FirstBuildUp build={build} />}
    {first && <FirstBurst build={build} />}
  </AbsoluteFill>;
};

const splitEmphasis = (line: string, words: string[]) => {
  const out: [string, boolean][] = [];
  let rest = line;
  while (rest) {
    let best: [number, string] | null = null;
    for (const w of words) {
      const i = w ? rest.indexOf(w) : -1;
      if (i >= 0 && (!best || i < best[0])) best = [i, w];
    }
    if (!best) {
      out.push([rest, false]);
      break;
    }
    if (best[0] > 0) out.push([rest.slice(0, best[0]), false]);
    out.push([best[1], true]);
    rest = rest.slice(best[0] + best[1].length);
  }
  return out;
};

// A fixed band of text (header or footer), centred in the safe area width.
const Band: React.FC<{band: TextBand}> = ({band}) => (
  <div style={{position: 'absolute', left: 0, width: 1080, top: band.top, height: band.height,
    background: band.background ?? 'transparent'}}>
    <div style={{position: 'absolute', left: 120, width: 780, top: 0, height: band.height, display: 'flex',
      flexDirection: 'column', justifyContent: 'center', textAlign: 'center', fontFamily: FONT, fontWeight: band.weight,
      fontSize: band.size, lineHeight: 1.25, color: band.color, whiteSpace: 'pre-line',
      ...(band.stroke ? {WebkitTextStroke: `${band.size * 0.16}px ${band.stroke}`, paintOrder: 'stroke fill'} : {})} as React.CSSProperties}>
      {(band.text ?? '').split('\n').map((line, i) => (
        <div key={i}>
          {splitEmphasis(line, band.emphasis).map(([seg, em], j) => (
            <span key={j} style={{color: em ? band.accent : band.color}}>{seg}</span>
          ))}
        </div>
      ))}
    </div>
  </div>
);

// The picture inside the format's frame: full bleed (optionally dimmed) or a box over a blurred copy.
const Framed: React.FC<{layout: Layout; children: React.ReactNode}> = ({layout, children}) => {
  const m = layout.media;
  if (m.mode === 'full') {
    return (
      <AbsoluteFill>
        {children}
        {m.dim > 0 && <AbsoluteFill style={{background: `rgba(0,0,0,${m.dim})`}} />}
      </AbsoluteFill>
    );
  }
  return (
    <AbsoluteFill>
      {m.fill === 'blur'
        ? <AbsoluteFill style={{filter: 'blur(40px) brightness(0.55)', transform: 'scale(1.15)'}}>{children}</AbsoluteFill>
        : <AbsoluteFill style={{background: m.fill_color}} />}
      <div style={{position: 'absolute', left: m.side, width: 1080 - 2 * m.side, top: m.top, height: m.height,
        borderRadius: m.radius, overflow: 'hidden'}}>
        {children}
      </div>
    </AbsoluteFill>
  );
};

const Progress: React.FC<{color: string; total: number}> = ({color, total}) => {
  const frame = useCurrentFrame();
  return <div style={{position: 'absolute', left: 0, top: 240, height: 8, background: color,
    width: `${Math.min(1, frame / Math.max(1, total - 1)) * 100}%`}} />;
};

export const Short: React.FC<ShortProps> = ({timeline}) => {
  const {fps, durationInFrames} = useVideoConfig();
  const frame = (sec: number) => Math.round(sec * fps);
  const span = (start: number, end: number) => Math.max(1, frame(end) - frame(start));
  const clips = [...timeline.clips].sort((a, b) => a.layer - b.layer);
  const layout = timeline.layout ?? null;
  const view = (clip: Clip) => {
    let inner = <ClipView clip={clip} frames={span(clip.start, clip.end)} />;
    if (clip.layer === 0 && MEDIA.has(clip.component) && (clip.props.transition === 'mirror'
      || ['top5', 'top3', 'top1'].includes(String(clip.props.tier)))) {
      inner = <MontageMedia clip={clip} frames={span(clip.start, clip.end)}>{inner}</MontageMedia>;
    }
    return layout && clip.layer === 0 && MEDIA.has(clip.component) ? <Framed layout={layout}>{inner}</Framed> : inner;
  };

  return (
    <LayoutContext.Provider value={layout}>
      <AbsoluteFill style={{backgroundColor: layout?.background ?? '#0f1419'}}>
        {clips.map((clip, i) => (
          <Sequence key={`c${i}`} from={frame(clip.start)} durationInFrames={span(clip.start, clip.end)} layout="none">
            {view(clip)}
          </Sequence>
        ))}
        {layout?.header && <Band band={layout.header} />}
        {layout?.footer && <Band band={layout.footer} />}
        {layout?.progress_bar && <Progress color={layout.progress_bar} total={durationInFrames} />}
        {timeline.captions.map((cap, i) => (
          <Sequence key={`t${i}`} from={frame(cap.start)} durationInFrames={span(cap.start, cap.end)} layout="none">
            <CaptionView caption={cap} />
          </Sequence>
        ))}
        {/* Audio is mixed by vg.mix for both Japanese rendering and English dubbing. */}
      </AbsoluteFill>
    </LayoutContext.Provider>
  );
};
