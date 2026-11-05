// Mirror of vg/schemas.py Timeline (renderer input). Paths are relative to the run directory,
// which is passed to Remotion as --public-dir, so they resolve through staticFile().

export type Clip = {
  start: number;
  end: number;
  layer: number;
  component: string;
  props: Record<string, unknown>;
};

export type RankCardProps = {rank: number; label: string; value?: string; bar?: number; color?: string};
export type CountdownProps = {from: number; to: number};
export type RankScrollProps = {
  items: {rank: number; label: string; src?: string}[];
  speed?: number; // pixels per second; negative reverses direction
};
export type CharacterPairProps = {
  a: {name: string; src: string; color: string};
  b: {name: string; src: string; color: string};
  roles?: {a: string; b: string}; // script roles; timeline assembly fills speaking
  speaking: {speaker: 'a' | 'b'; start: number; end: number}[];
};

export type Caption = {
  start: number;
  end: number;
  text: string; // line breaks already decided by vg (BudouX), '\n' separated
  style: string;
  emphasis: string[];
  speaker: string | null;
};

export type AudioClip = {
  start: number;
  src: string;
  kind: 'voice' | 'music' | 'sfx';
  gain_db: number;
  duck_under_voice: boolean;
  fade_in?: number; // seconds
  fade_out?: number;
  duration?: number | null; // measured source seconds; absent in legacy timelines
};

export type Timeline = {
  schema_version: number;
  width: number;
  height: number;
  fps: number;
  duration: number;
  clips: Clip[];
  captions: Caption[];
  audio: AudioClip[];
};

export type ShortProps = {timeline: Timeline};
