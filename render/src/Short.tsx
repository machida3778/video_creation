import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {CaptionView, FONT, LayoutContext} from './components/Caption';
import {ClipView} from './components/registry';
import type {Clip, Layout, ShortProps, TextBand} from './types';

// Base-layer components that show the scene's picture; a format's media frame places them.
const MEDIA = new Set(['ken_burns_image', 'video_clip']);

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
    const inner = <ClipView clip={clip} frames={span(clip.start, clip.end)} />;
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
