import { env } from 'next-runtime-env';
import { Settings } from '@/types/settings';

const STORAGE_KEY = 'settings';

// Remove the theme logic - theme should follow the same pattern as other env variables
export const getSettings = (): Settings => {
  let settings: Settings = {
    theme: 'light', // This will be overridden by the state management
  };
  
  const settingsJson = sessionStorage.getItem(STORAGE_KEY);
  if (settingsJson) {
    try {
      let savedSettings = JSON.parse(settingsJson) as Settings;
      settings = Object.assign(settings, savedSettings);
    } catch (e) {
      console.error(e);
    }
  }
  return settings;
};

export const saveSettings = (settings: Settings) => {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
};
