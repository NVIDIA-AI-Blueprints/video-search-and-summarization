// SPDX-License-Identifier: Apache-2.0

import type { ComponentProps, Ref, SyntheticEvent } from 'react';

type MutedVideoProps = Omit<ComponentProps<'video'>, 'muted' | 'ref'> & { ref?: Ref<HTMLVideoElement> };

function silence(video: HTMLVideoElement) {
  video.muted = true;
  video.volume = 0;
}

/**
 * A video that never plays sound: muted with volume 0, and muted again on any volume change or load, so nothing the user
 * clicks can turn audio on. The native mute and volume controls are hidden in styles.css. Use it for every video in the console.
 */
export function MutedVideo({ onVolumeChange, onLoadedMetadata, ref, children, ...props }: MutedVideoProps) {
  return (
    <video
      {...props}
      muted
      onLoadedMetadata={(event: SyntheticEvent<HTMLVideoElement>) => {
        silence(event.currentTarget);
        onLoadedMetadata?.(event);
      }}
      onVolumeChange={(event: SyntheticEvent<HTMLVideoElement>) => {
        const video = event.currentTarget;
        if (!video.muted || video.volume !== 0) silence(video);
        onVolumeChange?.(event);
      }}
      ref={ref}
    >
      {children ?? <track kind="captions" />}
    </video>
  );
}
