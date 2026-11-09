import React from 'react';
import {AbsoluteFill, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Caption, CaptionLook, Layout} from '../types';

export const FONT = '"Noto Sans CJK JP", "Noto Sans JP", sans-serif';

// The format layout of the video being rendered (Short.tsx provides it); null = default look.
export const LayoutContext = React.createContext<Layout | null>(null);

// Overlay components are drawn for the 780x1200 safe area at (120, 240). With a format layout they are
// moved below the header band and scaled to end above the caption block, so nothing overlaps.
export const contentArea = (layout: Layout | null): {top: number; scale: number} => {
  if (!layout) return {top: 240, scale: 1};
  const top = layout.header ? layout.header.top + layout.header.height + 10 : 240;
  const bottom = layout.caption.y - layout.caption.size * 1.45;
  // vg/schemas.py Layout guarantees bottom - top >= 420 px, so the scale never needs a floor.
  return {top, scale: Math.min(1, Math.max(0.01, (bottom - top) / 1200))};
};

export const useSafeArea = (base: React.CSSProperties): React.CSSProperties => {
  const {top, scale} = contentArea(React.useContext(LayoutContext));
  return scale === 1 && top === 240 ? base : {...base, top, transform: `scale(${scale})`, transformOrigin: '50% 0'};
};

// Format captions: HTML so a background box can follow each line. Centred on the safe area (x 120-900).
// Line breaks come from vg (BudouX) with the same size and width (vg/captions.py caption_box).
export const LookLines: React.FC<{text: string; emphasis?: string[]; look: CaptionLook; centerY: number}> = (
  {text, emphasis = [], look, centerY},
) => {
  const lines = text.split('\n');
  const lineH = look.size * 1.28;
  const stroke = look.stroke ? {WebkitTextStroke: `${look.size * look.stroke_ratio}px ${look.stroke}`,
    paintOrder: 'stroke fill'} : {};
  return (
    // Centred at x=510 within max_width; vg already broke the lines to fit (box padding and outline
    // included), so the browser must not wrap them again.
    <div style={{position: 'absolute', left: 510 - look.max_width / 2, width: look.max_width,
      top: centerY - (lines.length * lineH) / 2, textAlign: 'center', whiteSpace: 'nowrap', fontFamily: FONT,
      fontWeight: look.weight, fontSize: look.size, lineHeight: `${lineH}px`, color: look.fill}}>
      {lines.map((line, i) => (
        <div key={i}>
          <span style={{...stroke, ...(look.box ? {background: look.box, padding: `0 ${look.size * 0.25}px`,
            borderRadius: look.size * 0.12} : {})} as React.CSSProperties}>
            {splitEmphasis(line, emphasis).map(([seg, em], j) => (
              <span key={j} style={{color: em ? look.accent : look.fill, fontSize: look.size * (em ? 1.2 : 1)}}>{seg}</span>
            ))}
          </span>
        </div>
      ))}
    </div>
  );
};

// Safe area for essential text on 1080x1920 (types/*/type.yaml): x 120-900, captions around y 1050-1380.
const STYLES: Record<string, {fill: string; stroke: string; size: number; accent: string}> = {
  default: {fill: '#ffffff', stroke: '#111111', size: 66, accent: '#ffd400'},
  speaker_a: {fill: '#ffffff', stroke: '#2f7d32', size: 64, accent: '#ffe066'},
  speaker_b: {fill: '#ffffff', stroke: '#b0306a', size: 64, accent: '#ffe066'},
  title: {fill: '#ffffff', stroke: '#111111', size: 84, accent: '#ffd400'},
};

const splitEmphasis = (line: string, words: string[]) => {
  // [text, emphasized] segments; first match wins at each position.
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

export const CaptionLines: React.FC<{
  text: string;
  emphasis?: string[];
  style?: string;
  centerY: number;
  width?: number;
}> = ({text, emphasis = [], style = 'default', centerY, width = 1080}) => {
  const s = STYLES[style] ?? STYLES.default;
  const lines = text.split('\n');
  const lineH = s.size * 1.28;
  const top = centerY - (lines.length * lineH) / 2;
  return (
    <svg width={width} height={1920} style={{position: 'absolute', left: 0, top: 0}}>
      {lines.map((line, i) => (
        <text
          key={i}
          x={width / 2 - 30}
          y={top + lineH * (i + 0.8)}
          textAnchor="middle"
          fontFamily={FONT}
          fontWeight={900}
          fontSize={s.size}
          fill={s.fill}
          stroke={s.stroke}
          strokeWidth={s.size * 0.2}
          strokeLinejoin="round"
          paintOrder="stroke"
        >
          {splitEmphasis(line, emphasis).map(([seg, em], j) => (
            <tspan key={j} fill={em ? s.accent : s.fill} fontSize={s.size * (em ? 1.2 : 1)}>
              {seg}
            </tspan>
          ))}
        </text>
      ))}
    </svg>
  );
};

export const CaptionView: React.FC<{caption: Caption}> = ({caption}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const layout = React.useContext(LayoutContext);
  const pop = spring({frame, fps, config: {damping: 14, stiffness: 220}, durationInFrames: 8});
  if (layout) {
    const base = caption.style === 'title' && layout.title ? layout.title : layout.caption;
    // Dialogue keeps its speaker colours as the outline (or box) colour.
    const speaker = caption.style.startsWith('speaker_') ? STYLES[caption.style]?.stroke : undefined;
    const look = speaker ? {...base, ...(base.box ? {box: speaker} : {stroke: speaker})} : base;
    const scale = look.animation === 'pop' ? 0.92 + 0.08 * pop : 1;
    return (
      <AbsoluteFill style={{transform: `scale(${scale})`, transformOrigin: `510px ${look.y}px`}}>
        <LookLines text={caption.text} emphasis={caption.emphasis} look={look} centerY={look.y} />
      </AbsoluteFill>
    );
  }
  return (
    <AbsoluteFill style={{transform: `scale(${0.92 + 0.08 * pop})`, transformOrigin: '50% 63%'}}>
      <CaptionLines text={caption.text} emphasis={caption.emphasis} style={caption.style} centerY={1215} />
    </AbsoluteFill>
  );
};
