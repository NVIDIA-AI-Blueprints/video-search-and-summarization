// SPDX-License-Identifier: MIT
import { env } from 'next-runtime-env';

// Application naming constants with environment variable support
export const APPLICATION_TITLE = 
  env('NEXT_PUBLIC_APP_TITLE') || 
  process?.env?.NEXT_PUBLIC_APP_TITLE || 
  'VSS BLUEPRINT';

export const APPLICATION_SUBTITLE = 
  env('NEXT_PUBLIC_APP_SUBTITLE') || 
  process?.env?.NEXT_PUBLIC_APP_SUBTITLE || 
  '';
