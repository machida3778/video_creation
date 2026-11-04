import React from 'react';
import {Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import type {CountdownProps, RankCardProps, RankScrollProps} from '../types';
import {FONT} from './Caption';

// Clip every moving element to the platform safe area, including text and pop overshoot.
const safeArea: React.CSSProperties = {
  position: 'absolute', left: 120, top: 240, width: 780, height: 1200,
  overflow: 'hidden', fontFamily: FONT, color: '#fff', pointerEvents: 'none',
};
const text: React.CSSProperties = {
  position: 'absolute', left: 30, width: 720, textAlign: 'center', fontWeight: 900,
  overflowWrap: 'anywhere', whiteSpace: 'pre-line', lineHeight: 1.25,
  textShadow: '0 3px 6px #111, 0 -2px 4px #111',
};

export const RankCard: React.FC<{props: RankCardProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({frame, fps, config: {damping: 14, stiffness: 220}});
  const progress = interpolate(frame, [0, 0.6 * fps], [0, 1], {extrapolateRight: 'clamp'});
  const color = props.color ?? '#ffd400';
  return (
    <div style={safeArea}>
      <div style={{position: 'absolute', top: 100, left: 100, width: 580, height: 170,
        display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 36,
        background: color, color: '#111', fontSize: 96, fontWeight: 900,
        transform: `scale(${0.7 + 0.3 * pop})`, opacity: Math.min(1, pop)}}>
        第{props.rank}位
      </div>
      <div style={{...text, top: 320, fontSize: 66, maxHeight: 250, overflow: 'hidden'}}>{props.label}</div>
      {props.value !== undefined && (
        <div style={{...text, top: 590, fontSize: 64, maxHeight: 170, overflow: 'hidden'}}>{props.value}</div>
      )}
      {props.bar !== undefined && (
        <div style={{position: 'absolute', top: 800, left: 40, width: 700, height: 40,
          borderRadius: 20, overflow: 'hidden', background: '#111c'}}>
          <div style={{height: '100%', width: `${Math.max(0, Math.min(1, props.bar)) * progress * 100}%`,
            borderRadius: 20, background: color}} />
        </div>
      )}
    </div>
  );
};

export const Countdown: React.FC<{props: CountdownProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / (0.6 * fps);
  if (t >= 1) return null;
  const enter = Math.min(1, t / 0.2);
  const leave = Math.min(1, (1 - t) / 0.2);
  return (
    <div style={safeArea}>
      <div style={{...text, top: 1090, fontSize: 64, color: '#ffe066', opacity: enter * leave,
        transform: `translateY(${(1 - enter) * 25 - (1 - leave) * 25}px)`}}>
        {props.from} → {props.to}
      </div>
    </div>
  );
};

export const RankScroll: React.FC<{props: RankScrollProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  if (!props.items.length) return null;
  const pitch = 370;
  const cycle = props.items.length * pitch;
  const offset = ((frame / fps * (props.speed ?? 140)) % cycle + cycle) % cycle;
  // Repeat enough cards to cover the viewport even with only one item; wrap offscreen seamlessly.
  const cards = Array.from({length: props.items.length + 3}, (_, i) => props.items[i % props.items.length]);
  return (
    <div style={{...safeArea, background: '#102033'}}>
      <div style={{position: 'absolute', top: 210, left: 30, display: 'flex', gap: 30,
        transform: `translateX(${-offset}px)`}}>
        {cards.map((item, i) => (
          <div key={i} style={{width: 340, height: 660, flexShrink: 0, borderRadius: 28,
            background: '#223950', overflow: 'hidden', textAlign: 'center', fontWeight: 900}}>
            <div style={{height: 100, lineHeight: '100px', fontSize: 64, color: '#ffe066'}}>第{item.rank}位</div>
            {item.src && <Img src={staticFile(item.src)} style={{width: 300, height: 270, objectFit: 'cover', borderRadius: 18}} />}
            <div style={{padding: '24px 20px', fontSize: 56, lineHeight: 1.3, overflowWrap: 'anywhere',
              whiteSpace: 'pre-line'}}>{item.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
};
