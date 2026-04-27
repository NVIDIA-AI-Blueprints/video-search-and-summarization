import React from 'react';
import { Whisper, Tooltip } from 'rsuite';

type WhisperProps = React.ComponentProps<typeof Whisper>;

export interface VideoModalTooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  placement?: WhisperProps['placement'];
  trigger?: WhisperProps['trigger'];
  wrapperClassName?: string;
  zIndex?: number;
}

const getVideoModalContainer = (): HTMLElement => {
  const modal = document.querySelector('dialog#video-modal-id');
  return (modal as HTMLElement) ?? document.body;
};

export const VideoModalTooltip: React.FC<VideoModalTooltipProps> = ({
  content,
  children,
  placement = 'top',
  trigger = 'hover',
  wrapperClassName,
  zIndex = 2000,
}) => (
  <Whisper
    trigger={trigger}
    placement={placement}
    container={getVideoModalContainer}
    speaker={<Tooltip style={{ zIndex }}>{content}</Tooltip>}
  >
    <div className={wrapperClassName}>{children}</div>
  </Whisper>
);
