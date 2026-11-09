import React, {useEffect, useRef, useState} from 'react';
import {AbsoluteFill, continueRender, delayRender, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {RankLabelProps, SectionCardProps} from '../types';

const SERIF = '"Noto Serif CJK JP", serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const seed = (i: number, channel: number) => {
  const n = Math.sin((i + 1) * 127.1 + channel * 311.7) * 43758.5453;
  return n - Math.floor(n);
};
export const tierColor = (rank: number) => ({5: '#70baff', 4: '#7dff99', 3: '#ffa54a', 2: '#fff1c7', 1: '#ffd45c'}[rank] ?? '#fff');

// Measure the actual system font before capturing a frame, including long Latin names.
const FittedName: React.FC<{name: string}> = ({name}) => {
  const measure = useRef<HTMLSpanElement>(null);
  const [size, setSize] = useState(92);
  const [handle] = useState(() => delayRender('Measure ranking label'));
  useEffect(() => {
    let active = true;
    document.fonts.load(`900 92px ${SERIF}`).then(() => {
      if (active) {
        const width = measure.current?.getBoundingClientRect().width ?? 840;
        setSize(92 * Math.min(1, 840 / Math.max(1, width)));
        continueRender(handle);
      }
    }).catch(() => continueRender(handle));
    return () => {active = false; continueRender(handle);};
  }, [name, handle]);
  return <>
    <span ref={measure} style={{position: 'absolute', visibility: 'hidden', fontSize: 92, whiteSpace: 'pre'}}>{name}</span>
    <span style={{fontSize: size, whiteSpace: 'pre'}}>{name}</span>
  </>;
};

export const RankLabel: React.FC<{props: RankLabelProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const rankIn = interpolate(frame / fps, [0, 0.25], [0, 1], clamp);
  const nameIn = interpolate(frame / fps, [0.1, 0.35], [0, 1], clamp);
  const top = props.tier && props.tier !== 'normal';
  const color = props.color ?? (top ? tierColor(props.rank) : '#fff');
  const shadow = '0 3px 12px #000, 0 0 24px #0009';
  return <AbsoluteFill style={{fontFamily: SERIF, fontWeight: 900, color: '#fff', textShadow: shadow}}>
    <div style={{position: 'absolute', left: 80, width: 500, top: 1010, fontSize: 110, lineHeight: 1.2,
      textAlign: 'center', opacity: rankIn, transform: `translateY(${(1 - rankIn) * 30}px)`}}>{props.rank}位</div>
    <div style={{position: 'absolute', left: 95, width: 485, top: 1150, height: 10, background: '#fff',
      boxShadow: '0 3px 12px #0008', opacity: rankIn}} />
    <div style={{position: 'absolute', left: 80, top: 1240, width: 840, lineHeight: 1.2, color,
      textShadow: top ? `${shadow}, 0 0 18px ${color}, 0 0 42px ${color}` : shadow,
      opacity: nameIn, transform: `translateY(${(1 - nameIn) * 20}px)`}}><FittedName name={props.name} /></div>
  </AbsoluteFill>;
};

export const SectionCard: React.FC<{props: SectionCardProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const color = props.color ?? '#b97aff';
  return <AbsoluteFill style={{background: '#000', overflow: 'hidden'}}>
    {/* A broad text halo keeps the star card visible to black-frame QA, even between twinkles. */}
    <div style={{position: 'absolute', left: 190, top: 720, width: 700, height: 480, opacity: 0.5,
      background: `radial-gradient(ellipse, ${color}, transparent 72%)`}} />
    {Array.from({length: 110}, (_, i) => {
      const size = 1.5 + seed(i, 2) * 4;
      return <div key={i} style={{position: 'absolute', left: seed(i, 0) * 1080, top: seed(i, 1) * 1920,
        width: size, height: size, borderRadius: '50%', background: i % 4 ? '#fff' : color,
        opacity: 0.25 + 0.75 * (0.5 + 0.5 * Math.sin(frame / fps * (1 + seed(i, 3) * 3) + i)),
        boxShadow: `0 0 ${size * 3}px ${color}`}} />;
    })}
    <div style={{position: 'absolute', left: 120, top: 840, width: 840, height: 240, display: 'flex',
      justifyContent: 'center', alignItems: 'center', fontFamily: SERIF, fontSize: 120, fontWeight: 700,
      color, textShadow: `0 0 12px ${color}, 0 0 38px ${color}, 0 0 80px ${color}`}}>{props.text}</div>
  </AbsoluteFill>;
};

// Each spark's trajectory is a pure function of the scene-local frame and its fixed seed.
export const Sparks: React.FC<{rank: number; color?: string}> = ({rank, color}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return <AbsoluteFill style={{overflow: 'hidden', pointerEvents: 'none'}}>
    {Array.from({length: rank === 1 ? 60 : 32}, (_, i) => {
      const age = (frame / fps * (0.3 + seed(i, 4) * 0.4) + seed(i, 5)) % 1;
      return <div key={i} style={{position: 'absolute', left: seed(i, 0) * 1080 + Math.sin(i) * age * 110,
        top: 1920 - age * (900 + seed(i, 1) * 1200), width: 2 + seed(i, 2) * 4,
        height: 5 + seed(i, 3) * 15, transform: `rotate(${i * 29}deg)`,
        opacity: Math.sin(age * Math.PI) * 0.85, background: color ?? tierColor(rank),
        boxShadow: `0 0 12px ${color ?? tierColor(rank)}`, borderRadius: '50%'}} />;
    })}
  </AbsoluteFill>;
};
