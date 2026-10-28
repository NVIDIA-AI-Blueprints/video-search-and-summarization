// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Tabler stand-ins for the Material icons used by warehouse analytics.
 */
import React from 'react';
import {
  IconAlertCircle,
  IconAlertTriangle,
  IconBan,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconCircleFilled,
  IconInfoCircle,
  IconMaximize,
  IconMenu2,
  IconMinus,
  IconPhotoOff,
  IconPlayerPause,
  IconPlayerPlay,
  IconRefresh,
  IconSettings,
  IconX,
} from '@tabler/icons-react';

type IconProps = {
  className?: string;
  style?: React.CSSProperties;
  onClick?: React.MouseEventHandler;
  title?: string;
  htmlColor?: string;
  fontSize?: 'inherit' | 'small' | 'medium' | 'large' | number;
  color?: string;
};

const sizeOf = (fontSize: IconProps['fontSize']): number => {
  if (typeof fontSize === 'number') return fontSize;
  if (fontSize === 'small') return 16;
  if (fontSize === 'large') return 32;
  return 20;
};

const wrap = (Icon: React.ComponentType<{ size?: number; className?: string; style?: React.CSSProperties; color?: string; onClick?: React.MouseEventHandler }>) => {
  const Wrapped = ({ className, style, onClick, title, htmlColor, fontSize, color, ...rest }: IconProps) => (
    <span title={title} className={className} style={{ display: 'inline-flex', ...style }} onClick={onClick} {...rest}>
      <Icon
        size={sizeOf(fontSize)}
        color={htmlColor || color}
      />
    </span>
  );
  Wrapped.displayName = `WarehouseIcon(${Icon.displayName || Icon.name || 'Icon'})`;
  return Wrapped;
};

export const Cancel = wrap(IconX);
export const CancelIcon = Cancel;
export const Circle = wrap(IconCircleFilled);
export const CircleIcon = Circle;
export const Close = wrap(IconX);
export const CloseIcon = Close;
export const DoNotDisturbOnOutlined = wrap(IconBan);
export const DoNotDisturbOnOutlinedIcon = DoNotDisturbOnOutlined;
export const DoneOutline = wrap(IconCheck);
export const DoneOutlineIcon = DoneOutline;
export const Error = wrap(IconAlertCircle);
export const ErrorIcon = Error;
export const ErrorOutline = wrap(IconAlertCircle);
export const ErrorOutlineIcon = ErrorOutline;
export const ExpandLess = wrap(IconChevronUp);
export const ExpandLessIcon = ExpandLess;
export const ExpandMore = wrap(IconChevronDown);
export const ExpandMoreIcon = ExpandMore;
export const Fullscreen = wrap(IconMaximize);
export const FullscreenIcon = Fullscreen;
export const FullscreenExit = wrap(IconMinus);
export const FullscreenExitIcon = FullscreenExit;
export const HideImage = wrap(IconPhotoOff);
export const HideImageIcon = HideImage;
export const Info = wrap(IconInfoCircle);
export const InfoIcon = Info;
export const InfoOutlined = wrap(IconInfoCircle);
export const InfoOutlinedIcon = InfoOutlined;
export const Menu = wrap(IconMenu2);
export const MenuIcon = Menu;
export const Minimize = wrap(IconMinus);
export const MinimizeIcon = Minimize;
export const NotInterested = wrap(IconBan);
export const NotInterestedIcon = NotInterested;
export const PauseCircle = wrap(IconPlayerPause);
export const PauseCircleIcon = PauseCircle;
export const PlayCircle = wrap(IconPlayerPlay);
export const PlayCircleIcon = PlayCircle;
export const PlayDisabled = wrap(IconBan);
export const PlayDisabledIcon = PlayDisabled;
export const Refresh = wrap(IconRefresh);
export const RefreshIcon = Refresh;
export const Settings = wrap(IconSettings);
export const SettingsIcon = Settings;
export const Warning = wrap(IconAlertTriangle);
export const WarningIcon = Warning;

export default {
  Cancel, Close, Error, Info, Menu, Settings, Warning,
};
