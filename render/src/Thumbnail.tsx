import React from 'react';
import {AbsoluteFill, Img, staticFile} from 'remotion';
import {FONT} from './components/Caption';

type Box = {x: number; y: number; width: number; height: number};
export type ThumbnailProps = {
  width: number; height: number; src: string;
  // A collage: 2–3 pictures side by side (e.g. the talents of a ranking); `src` alone is a single picture.
  srcs?: string[];
  option: {text: string; emphasis: string[]; palette: 'yellow' | 'red' | 'white'; badge: string | null};
  layout: {lines: string[]; font_size: number; line_height: number; padding: number; box: Box; badge: Box | null;
    shade?: 'low' | null};
  arrow?: {x: number; y: number; angle: number};
};
const palettes = {yellow: '#ffd400', red: '#ff3b30', white: '#ffffff'};
const contained = (b: Box) => b.x >= 108 && b.y >= 480 && b.x + b.width <= 864 && b.y + b.height <= 1440;

export const validateThumbnail = (props: ThumbnailProps) => {
  const {layout: l} = props;
  if (props.width !== 1080 || props.height !== 1920 || l.font_size < 126 || l.lines.length > 3 ||
      !contained(l.box) || (l.badge && !contained(l.badge))) {
    throw new Error('Thumbnail layout failed: shorten the headline; minimum glyph size is 126 px');
  }
  if (props.arrow && (!Number.isFinite(props.arrow.angle) ||
      !contained({x: props.arrow.x - 100, y: props.arrow.y - 100, width: 200, height: 200}))) {
    throw new Error('Thumbnail arrow must stay inside the safe region');
  }
  return {width: props.width, height: props.height, durationInFrames: 1, fps: 30};
};

export const Thumbnail: React.FC<ThumbnailProps> = (props) => {
  validateThumbnail(props);
  const {layout: l, option, arrow} = props;
  const color = palettes[option.palette];
  // Mark emphasis before wrapping, so words crossing an automatic line break retain colour.
  const emphasized = new Set<number>();
  const flat = option.text.replaceAll('\n', '');
  for (const word of option.emphasis) {
    let start = flat.indexOf(word);
    while (word && start >= 0) {
      for (let i = start; i < start + word.length; i++) emphasized.add(i);
      start = flat.indexOf(word, start + word.length);
    }
  }
  let offset = 0;
  const collage = (props.srcs ?? []).length > 1 ? props.srcs! : null;
  return <AbsoluteFill style={{background: '#000'}}>
    {collage
      // Full-height columns: each 9:16 still keeps its face (upper part) visible above the low headline.
      ? <AbsoluteFill style={{display: 'flex', gap: 10}}>
        {collage.map((src, i) => <div key={i} style={{flex: 1, height: '100%', overflow: 'hidden'}}>
          <Img src={staticFile(src)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
        </div>)}
      </AbsoluteFill>
      : props.src && <Img src={staticFile(props.src)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />}
    <AbsoluteFill style={{background: 'radial-gradient(ellipse, transparent 35%, rgba(0,0,0,0.5) 100%)'}} />
    {l.shade === 'low' && <AbsoluteFill style={{
      background: 'linear-gradient(180deg, transparent 40%, rgba(0,0,0,0.45) 62%, rgba(0,0,0,0.6) 100%)'}} />}
    <svg width={props.width} height={props.height} style={{position: 'absolute', fontFamily: FONT, fontWeight: 900}}>
      {l.badge && option.badge && <g>
        <rect {...l.badge} rx={32} fill={color} />
        {Array.from(option.badge).map((char, i) => <text key={i} x={l.badge!.x + 20 + (i + 0.5) * 140}
          y={l.badge!.y + 150} fontSize={140} fill="#000" textAnchor="middle">{char}</text>)}
      </g>}
      <g style={{filter: 'drop-shadow(0px 3px 3px rgba(0,0,0,0.6))'}}>
        {l.lines.map((line, row) => {
          const start = offset;
          offset += line.length;
          let codeOffset = 0;
          return <g key={row}>{Array.from(line).map((char, col) => {
            const em = emphasized.has(start + codeOffset);
            codeOffset += char.length;
            return <text key={col} x={486 + (col + 0.5 - Array.from(line).length / 2) * l.font_size}
              y={l.box.y + l.padding + l.line_height * row + l.font_size}
              fontSize={l.font_size} textAnchor="middle" fill={em ? color : '#ffffff'}
              stroke="#000000" strokeWidth={l.font_size * 0.08} strokeLinejoin="round" paintOrder="stroke">{char}</text>;
          })}</g>;
        })}
      </g>
      {arrow && <g transform={`translate(${arrow.x} ${arrow.y}) rotate(${arrow.angle})`}>
        <path d="M -70 0 L 60 0 M 20 -40 L 60 0 L 20 40" fill="none" stroke="#000" strokeWidth={26} strokeLinecap="round" strokeLinejoin="round" />
        <path d="M -70 0 L 60 0 M 20 -40 L 60 0 L 20 40" fill="none" stroke={color} strokeWidth={14} strokeLinecap="round" strokeLinejoin="round" />
      </g>}
    </svg>
  </AbsoluteFill>;
};
