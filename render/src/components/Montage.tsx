import React, {useEffect, useRef, useState} from 'react';
import {AbsoluteFill, continueRender, delayRender, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {RankLabelProps, SectionCardProps} from '../types';

// Every effect here is a pure function of the scene-local frame and fixed seeds (deterministic renders), and
// all motion is continuous: no framing cuts or punch-ins inside a picture (user taste, 2026-11-05).
const SERIF = '"Noto Serif CJK JP", serif';
const GOLD = '#ffd45c';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const easeOut = {...clamp, easing: Easing.out(Easing.cubic)};
export const seed = (i: number, channel: number) => {
  const n = Math.sin((i + 1) * 127.1 + channel * 311.7) * 43758.5453;
  return n - Math.floor(n);
};
export const tierColor = (rank: number) => ({5: '#70baff', 4: '#7dff99', 3: '#ffa54a', 2: '#fff1c7', 1: '#ffd45c'}[rank] ?? '#fff');

// Seconds of the 1st-place build-up before the reveal: the label's `reveal`, else 1.6 s when the scene is long
// enough. At least 2.5 s are always left for the revealed picture.
export const revealSec = (value: unknown, sceneSec: number) => {
  const room = Math.max(0, sceneSec - 2.5);
  if (typeof value === 'number' && Number.isFinite(value)) return Math.min(Math.max(0, value), room);
  return sceneSec >= 5 ? Math.min(1.6, room) : 0;
};

// A highlight sweeping across text: `shine` 100 -> 0 moves it from left to right; null hides it.
const shineStyle = (shine: number): React.CSSProperties => ({
  position: 'absolute', left: 0, top: 0, whiteSpace: 'pre', color: 'transparent', textShadow: 'none',
  backgroundImage: 'linear-gradient(105deg, transparent 42%, rgba(255,255,255,0.95) 50%, transparent 58%)',
  backgroundSize: '300% 100%', backgroundRepeat: 'no-repeat', backgroundPosition: `${shine}% 0`,
  WebkitBackgroundClip: 'text', backgroundClip: 'text',
});

// Measure the actual system font before capturing a frame, including long Latin names.
const FittedName: React.FC<{name: string; shine?: number | null}> = ({name, shine = null}) => {
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
    <span style={{position: 'relative', display: 'inline-block', fontSize: size, whiteSpace: 'pre'}}>
      {name}
      {shine !== null && <span aria-hidden="true" style={shineStyle(shine)}>{name}</span>}
    </span>
  </>;
};

// One sweep at 0.6 s; top tiers sweep again every 1.8 s.
const sweep = (t: number, repeat: boolean) => {
  const local = repeat && t > 0.6 ? (t - 0.6) % 1.8 : t - 0.6;
  return local >= 0 && local <= 0.7 ? 100 - 100 * (local / 0.7) : null;
};

export const RankLabel: React.FC<{props: RankLabelProps; frames?: number}> = ({props, frames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sceneSec = (frames ?? Math.round(fps * 3.4)) / fps;
  const first = props.tier === 'top1';
  const build = first ? revealSec(props.reveal, sceneSec) : 0;
  const t = frame / fps - build;
  if (t < 0) return null;  // the 1st place appears with the flash
  const top = Boolean(props.tier && props.tier !== 'normal');
  const color = props.color ?? (top ? tierColor(props.rank) : '#fff');
  const shadow = '0 3px 12px #000, 0 0 24px #0009';
  // The rank slides in from the left on a spring (a bigger, bouncier entrance for the 1st place).
  const rankIn = spring({frame: Math.round(t * fps), fps,
    config: first ? {damping: 8, stiffness: 110} : {damping: 14, stiffness: 170}});
  const rankScale = first ? 1 + 0.8 * (1 - rankIn) : 1;
  const lineIn = interpolate(t, [0.08, 0.42], [0, 1], easeOut);
  const nameIn = interpolate(t, [0.16, 0.6], [0, 1], easeOut);
  // Labels leave with their picture instead of vanishing on the cut; the 1st place stays to the end.
  const out = first ? 1 : interpolate(t, [sceneSec - 0.2, sceneSec - 0.02], [1, 0], clamp);
  const float = Math.sin(t * 2.2) * 4;
  const glow = top ? `${shadow}, 0 0 18px ${color}, 0 0 42px ${color}` : shadow;
  return <AbsoluteFill style={{fontFamily: SERIF, fontWeight: 900, color: '#fff', textShadow: shadow, opacity: out}}>
    <div style={{position: 'absolute', left: 80, width: 500, top: 1010, fontSize: 110, lineHeight: 1.2,
      textAlign: 'center', color: first ? GOLD : '#fff', textShadow: first ? glow : shadow,
      opacity: Math.min(1, rankIn * 1.5),
      transform: `translate(${(1 - rankIn) * -140}px, ${float}px) scale(${rankScale})`}}>{props.rank}位</div>
    <div style={{position: 'absolute', left: 95, width: 485, top: 1150, height: 10, background: first ? GOLD : '#fff',
      boxShadow: first ? `0 0 18px ${GOLD}` : '0 3px 12px #0008', transformOrigin: '0 50%',
      transform: `scaleX(${lineIn})`}} />
    <div style={{position: 'absolute', left: 80, top: 1240, width: 840, lineHeight: 1.2, color, textShadow: glow,
      clipPath: `inset(-80px calc(${(1 - nameIn) * 100}% - ${nameIn * 80}px) -80px -80px)`,
      transform: `translate(${(1 - nameIn) * 40}px, ${float * 0.6}px)`}}>
      <FittedName name={props.name} shine={sweep(t, top)} />
    </div>
  </AbsoluteFill>;
};

export const SectionCard: React.FC<{props: SectionCardProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const color = props.color ?? '#b97aff';
  // The text comes slowly towards the viewer while its letters close up; the stars stream past.
  const zoom = interpolate(t, [0, 1.6], [0.86, 1.05], easeOut);
  const spacing = interpolate(t, [0, 0.7], [0.35, 0.04], easeOut);
  const flare = interpolate(t, [0.1, 0.5, 1.4], [0, 1, 0.35], clamp);
  // One line: long titles get a smaller size instead of wrapping mid-word.
  const size = Math.min(120, 800 / (Array.from(props.text).length * 1.04));
  return <AbsoluteFill style={{background: '#000', overflow: 'hidden'}}>
    {/* A broad text halo keeps the star card visible to black-frame QA, even between twinkles. */}
    <div style={{position: 'absolute', left: 190, top: 720, width: 700, height: 480, opacity: 0.5,
      background: `radial-gradient(ellipse, ${color}, transparent 72%)`}} />
    {Array.from({length: 110}, (_, i) => {
      const size = 1.5 + seed(i, 2) * 4;
      const y = ((seed(i, 1) * 1920 + t * (40 + seed(i, 6) * 160)) % 1920);
      return <div key={i} style={{position: 'absolute', left: seed(i, 0) * 1080, top: y,
        width: size, height: size, borderRadius: '50%', background: i % 4 ? '#fff' : color,
        opacity: 0.25 + 0.75 * (0.5 + 0.5 * Math.sin(t * (1 + seed(i, 3) * 3) + i)),
        boxShadow: `0 0 ${size * 3}px ${color}`}} />;
    })}
    {/* A thin horizontal flare through the text. */}
    <div style={{position: 'absolute', left: 540 - 520 * flare, width: 1040 * flare, top: 958, height: 4,
      opacity: flare, background: `linear-gradient(90deg, transparent, #fff, ${color}, #fff, transparent)`,
      boxShadow: `0 0 24px ${color}`}} />
    <div style={{position: 'absolute', left: 120, top: 840, width: 840, height: 240, display: 'flex',
      justifyContent: 'center', alignItems: 'center', fontFamily: SERIF, fontSize: size, fontWeight: 700,
      whiteSpace: 'nowrap', letterSpacing: `${spacing}em`, transform: `scale(${zoom})`,
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

// Soft out-of-focus lights drifting upwards over every item, so no picture is ever still.
export const Bokeh: React.FC<{color?: string}> = ({color = '#ffffff'}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  return <AbsoluteFill style={{overflow: 'hidden', pointerEvents: 'none', mixBlendMode: 'screen'}}>
    {Array.from({length: 14}, (_, i) => {
      const size = 30 + seed(i, 7) * 90;
      const x = seed(i, 8) * 1080 + Math.sin(t * 0.6 + i) * 30;
      const y = ((seed(i, 9) * 2100 - t * (25 + seed(i, 10) * 45)) % 2100 + 2100) % 2100 - 90;
      return <div key={i} style={{position: 'absolute', left: x - size / 2, top: y - size / 2, width: size,
        height: size, borderRadius: '50%', filter: 'blur(2px)',
        opacity: 0.1 + 0.18 * (0.5 + 0.5 * Math.sin(t * (0.8 + seed(i, 11)) + i * 1.7)),
        background: `radial-gradient(circle, ${color} 0%, transparent 70%)`}} />;
    })}
  </AbsoluteFill>;
};

// One diagonal glint crossing the picture shortly after it arrives.
export const LightSweep: React.FC<{start: number; strength?: number}> = ({start, strength = 0.28}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps - start;
  if (t < 0 || t > 0.8) return null;
  const x = interpolate(t, [0, 0.8], [-700, 1500], {...clamp, easing: Easing.inOut(Easing.quad)});
  return <AbsoluteFill style={{overflow: 'hidden', pointerEvents: 'none', mixBlendMode: 'screen'}}>
    <div style={{position: 'absolute', left: x, top: -400, width: 260, height: 2800, transform: 'rotate(18deg)',
      background: `linear-gradient(90deg, transparent, rgba(255,255,255,${strength}), transparent)`}} />
  </AbsoluteFill>;
};

// 1st place, before the reveal: a pulsing gold 「1位」 over the darkened picture while light streams inwards.
export const FirstBuildUp: React.FC<{build: number}> = ({build}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  if (build <= 0 || t >= build) return null;
  const k = t / build;
  const phase = 2 * Math.PI * (1.4 * t + (1.6 * t * t) / build);  // a heartbeat that speeds up
  const beat = Math.pow(Math.max(0, Math.sin(phase)), 6);
  const glow = 20 + 60 * k + 30 * beat;
  return <AbsoluteFill style={{pointerEvents: 'none', overflow: 'hidden'}}>
    {/* A gold halo keeps the build-up visible (also to black-frame QA). */}
    <div style={{position: 'absolute', left: 90, top: 520, width: 900, height: 680,
      opacity: 0.35 + 0.35 * k + 0.2 * beat, background: `radial-gradient(ellipse, ${GOLD}, transparent 70%)`}} />
    {Array.from({length: 22}, (_, i) => {
      const life = (t * (0.6 + seed(i, 13) * 0.8) + seed(i, 14)) % 1;
      return <div key={i} style={{position: 'absolute', left: 540, top: 860, width: 120 + 220 * life, height: 3,
        transformOrigin: '0 50%', transform: `rotate(${seed(i, 12) * 360}deg) translateX(${1100 * (1 - life)}px)`,
        background: `linear-gradient(90deg, ${GOLD}, transparent)`,
        opacity: (0.25 + 0.6 * k) * Math.sin(life * Math.PI)}} />;
    })}
    <div style={{position: 'absolute', left: 0, width: 1080, top: 700, height: 320, display: 'flex',
      justifyContent: 'center', alignItems: 'center', fontFamily: SERIF, fontWeight: 900, fontSize: 230,
      color: GOLD, transform: `scale(${0.85 + 0.25 * k + 0.07 * beat})`,
      textShadow: `0 0 ${glow * 0.3}px ${GOLD}, 0 0 ${glow}px ${GOLD}, 0 4px 18px #000`}}>1位</div>
  </AbsoluteFill>;
};

const CONFETTI = [GOLD, '#fff6d8', '#ffb347', '#ffffff'];

// 1st place, the reveal: a white flash, rotating gold rays from behind the face, a confetti burst and a gold
// glow around the frame.
export const FirstBurst: React.FC<{build: number}> = ({build}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = frame / fps - build;
  if (a < -0.1) return null;
  const flash = interpolate(a, [-0.1, 0, 0.5], [0, 1, 0], {...clamp, easing: Easing.out(Easing.quad)});
  if (a < 0) return <AbsoluteFill style={{background: '#fff', opacity: flash}} />;
  const rays = interpolate(a, [0, 0.35, 3], [0, 0.5, 0.28], clamp);
  // The rays come from behind the face: clear in the middle, strongest around it, fading at the edges.
  const mask = 'radial-gradient(circle at 50% 30%, transparent 0%, transparent 14%, #000 32%, #000 48%, transparent 85%)';
  return <AbsoluteFill style={{pointerEvents: 'none', overflow: 'hidden'}}>
    <AbsoluteFill style={{mixBlendMode: 'screen', opacity: rays, WebkitMaskImage: mask, maskImage: mask,
      background: `repeating-conic-gradient(from ${a * 22}deg at 50% 30%, rgba(255,214,110,0.9) 0deg 4deg, `
        + 'rgba(255,214,110,0) 9deg 20deg)'}} />
    {a < 3.2 && Array.from({length: 90}, (_, i) => {
      const direction = seed(i, 15) * Math.PI * 2;
      const speed = 700 + seed(i, 16) * 1100;
      const travel = (1 - Math.exp(-1.6 * a)) / 1.6;  // air drag
      const x = 540 + Math.cos(direction) * speed * travel;
      const y = 620 + (Math.sin(direction) * speed - 500) * travel + 450 * a * a;
      return <div key={i} style={{position: 'absolute', left: x, top: y, width: 12, height: 22,
        background: CONFETTI[i % CONFETTI.length], boxShadow: `0 0 8px ${GOLD}`,
        opacity: interpolate(a, [2.2, 3.2], [1, 0], clamp),
        transform: `rotate(${a * (200 + 400 * seed(i, 17)) + i * 40}deg) scaleY(${Math.cos(a * 8 + i)})`}} />;
    })}
    <AbsoluteFill style={{boxShadow: `inset 0 0 ${50 + 25 * Math.sin(a * 3)}px ${GOLD}`,
      opacity: interpolate(a, [0, 0.4], [0, 1], clamp)}} />
    <AbsoluteFill style={{background: '#fff', opacity: flash}} />
  </AbsoluteFill>;
};
