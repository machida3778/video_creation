import React from 'react';
import {Composition} from 'remotion';
import {Short} from './Short';
import type {ShortProps, Timeline} from './types';

const preview: Timeline = {
  schema_version: 0,
  width: 1080,
  height: 1920,
  fps: 30,
  duration: 4,
  clips: [{start: 0, end: 4, layer: 0, component: 'title_card', props: {text: 'プレビュー', sub: 'timeline.json を --props で渡す'}}],
  captions: [{start: 0.5, end: 3.5, text: '字幕のサンプルです', style: 'default', emphasis: ['字幕'], speaker: null}],
  audio: [],
};

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Short"
    component={Short}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={120}
    defaultProps={{timeline: preview} satisfies ShortProps}
    calculateMetadata={({props}) => {
      const t = (props as ShortProps).timeline;
      return {durationInFrames: Math.max(1, Math.round(t.duration * t.fps)), fps: t.fps, width: t.width, height: t.height};
    }}
  />
);
