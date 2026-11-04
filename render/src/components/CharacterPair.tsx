import React from 'react';
import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import type {CharacterPairProps} from '../types';
import {FONT} from './Caption';

export const CharacterPair: React.FC<{props: CharacterPairProps}> = ({props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const time = frame / fps;
  const active = (props.speaking ?? []).filter((s) => s.start <= time && time < s.end);
  return (
    <div style={{position: 'absolute', left: 120, top: 240, width: 780, height: 1200,
      overflow: 'hidden', pointerEvents: 'none', fontFamily: FONT}}>
      {(['a', 'b'] as const).map((speaker, i) => {
        const character = props[speaker];
        const interval = active.find((s) => s.speaker === speaker);
        const bounce = interval ? -3 * (1 - Math.cos(2 * Math.PI * 6 * (time - interval.start))) : 0;
        return (
          <div key={speaker} style={{position: 'absolute', top: 530, left: i === 0 ? 60 : 460, width: 260,
            opacity: active.length && !interval ? 0.6 : 1}}>
            <div style={{width: 260, height: 260, boxSizing: 'border-box', borderRadius: '50%', overflow: 'hidden',
              border: `8px solid ${interval ? character.color : 'transparent'}`, background: '#eee',
              transform: `translateY(${bounce}px) scale(${interval ? 1.08 : 1})`}}>
              <Img src={staticFile(character.src)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
            </div>
            <div style={{marginTop: 28, padding: '4px 8px', boxSizing: 'border-box', borderRadius: 12,
              background: '#111d', color: '#fff', borderBottom: `5px solid ${character.color}`,
              fontSize: 44, fontWeight: 900, textAlign: 'center', overflowWrap: 'anywhere',
              lineHeight: 1.2, maxHeight: 116, overflow: 'hidden'}}>{character.name}</div>
          </div>
        );
      })}
    </div>
  );
};
