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
export type RankLabelProps = {rank: number; name: string; tier?: 'normal' | 'top5' | 'top3' | 'top1'; color?: string};
export type SectionCardProps = {text: string; color?: string};
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
  layout?: Layout | null; // from a format profile; absent = default look
};

export type ShortProps = {timeline: Timeline};

// Mirror of vg/schemas.py Layout (format profiles).
export type TextBand = {
  text: string | null; top: number; height: number; background: string | null; color: string;
  accent: string; stroke: string | null; size: number; weight: number; emphasis: string[];
};
export type CaptionLook = {
  y: number; size: number; fill: string; stroke: string | null; stroke_ratio: number; accent: string;
  box: string | null; weight: number; max_width: number; animation: 'pop' | 'none';
};
export type MediaFrame = {
  mode: 'full' | 'box'; top: number; height: number; side: number; radius: number;
  fill: 'blur' | 'color'; fill_color: string; dim: number;
};
export type Layout = {
  background: string; media: MediaFrame; header: TextBand | null; footer: TextBand | null;
  caption: CaptionLook; title: CaptionLook | null; progress_bar: string | null;
};
