import React from 'react';
import {AbsoluteFill, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Caption} from '../types';

export const FONT = '"Noto Sans CJK JP", "Noto Sans JP", sans-serif';

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
  const pop = spring({frame, fps, config: {damping: 14, stiffness: 220}, durationInFrames: 8});
  return (
    <AbsoluteFill style={{transform: `scale(${0.92 + 0.08 * pop})`, transformOrigin: '50% 63%'}}>
      <CaptionLines text={caption.text} emphasis={caption.emphasis} style={caption.style} centerY={1215} />
    </AbsoluteFill>
  );
};
