// SPDX-License-Identifier: MIT
import { useEffect, useState } from 'react';
import { env } from 'next-runtime-env';

export type Theme = 'light' | 'dark';

const getDefaultTheme = (): Theme => {
  // Default to dark theme if no environment variable is set
  let isDarkThemeDefault = true;
  
  try {
    // Priority 1: Check runtime env (from __ENV.js)
    const envValue1 = env('NEXT_PUBLIC_DARK_THEME_DEFAULT');
    
    // Priority 2: Check build-time process.env
    const envValue2 = process?.env?.NEXT_PUBLIC_DARK_THEME_DEFAULT;
    
    // Prioritize env() over process.env
    let selectedValue: string | undefined = undefined;
    
    if (envValue1 !== undefined && envValue1 !== null && envValue1 !== '') {
      selectedValue = String(envValue1).trim().toLowerCase();
    } else if (envValue2 !== undefined && envValue2 !== null && envValue2 !== '') {
      selectedValue = String(envValue2).trim().toLowerCase();
    }
    
    // If a valid environment variable is set, use it
    if (selectedValue === 'true') {
      isDarkThemeDefault = true;
    } else if (selectedValue === 'false') {
      isDarkThemeDefault = false;
    }
    // Otherwise keep the default (true - dark theme)
    
  } catch (error) {
    // If there's any error reading env vars, default to dark theme
    console.warn('Error reading theme environment variables:', error);
    isDarkThemeDefault = true;
  }
  
  return isDarkThemeDefault ? 'dark' : 'light';
};

export const useTheme = () => {
  // Always start with the server-side default to prevent hydration mismatch
  const [theme, setTheme] = useState<Theme>(getDefaultTheme);
  const [isHydrated, setIsHydrated] = useState(false);

  useEffect(() => {
    // After hydration, check for saved theme preference or re-read environment variable
    const savedTheme = sessionStorage.getItem('lightMode');
    if (savedTheme && (savedTheme === 'light' || savedTheme === 'dark')) {
      // User has a saved preference - use it
      setTheme(savedTheme as Theme);
    } else {
      // No saved preference - ensure we use the environment variable default
      // Re-read here because env() might not be available during initial useState
      const envDefault = getDefaultTheme();
      setTheme(envDefault);
    }
    setIsHydrated(true);
  }, []);

  useEffect(() => {
    // Only apply theme changes after hydration
    if (!isHydrated) return;
    
    // Apply theme class to document
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }

    // Save to sessionStorage to persist user preference
    sessionStorage.setItem('lightMode', theme);
  }, [theme, isHydrated]);

  const toggleTheme = () => {
    setTheme(prevTheme => prevTheme === 'light' ? 'dark' : 'light');
  };

  return {
    theme,
    setTheme,
    toggleTheme,
    isDark: theme === 'dark',
    isLight: theme === 'light',
  };
};
