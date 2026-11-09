import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {FONT} from './Caption';

// start is relative to the enclosing scene/clip, in seconds (same clock as every overlay).
export const CountdownRing: React.FC<{seconds: number; start: number}> = ({seconds, start}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const localFrame = frame - Math.round(start * fps);
  const frames = Math.max(1, Math.round(seconds * fps));
  if (!Number.isFinite(seconds) || seconds <= 0 || localFrame < 0 || localFrame >= frames) return null;
  const progress = Math.min(1, localFrame / Math.max(1, frames - 1));
  const radius = 108;
  const circumference = 2 * Math.PI * radius;
  const angle = -Math.PI / 2 + 2 * Math.PI * progress;
  const color = `rgb(${Math.round(190 + 65 * progress)}, 235, ${Math.round(40 * (1 - progress))})`;
  return <svg width={280} height={280} viewBox="0 0 280 280"
    style={{position: 'absolute', left: 400, top: 620, overflow: 'visible'}}>
    <circle cx={140} cy={140} r={radius} fill="rgba(0,0,0,0.32)" stroke="white" strokeWidth={5} />
    <circle cx={140} cy={140} r={radius} fill="none" stroke={color} strokeWidth={13}
      strokeDasharray={circumference} strokeDashoffset={circumference * (1 - progress)}
      strokeLinecap="round" transform="rotate(-90 140 140)" />
    <circle cx={140 + radius * Math.cos(angle)} cy={140 + radius * Math.sin(angle)} r={10} fill="#00edff" />
    <text x={140} y={144} textAnchor="middle" dominantBaseline="central"
      style={{fontFamily: FONT, fontSize: 112, fontWeight: 900, fontStyle: 'italic', fill: 'white'}}>
      {Math.max(0, Math.ceil(seconds * (1 - progress)))}
    </text>
  </svg>;
};
