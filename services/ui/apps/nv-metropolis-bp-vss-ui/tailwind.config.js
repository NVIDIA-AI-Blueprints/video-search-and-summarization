// SPDX-License-Identifier: MIT
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx}',
    './pages/**/*.{js,ts,jsx,tsx}',
    './components/**/*.{js,ts,jsx,tsx}',
    '../../packages/nv-metropolis-bp-vss-ui/*/lib/**/*.{js,jsx}',
    '../../packages/nv-metropolis-bp-vss-ui/*/lib-src/**/*.{ts,tsx}',
    '../../packages/nemo-agent-toolkit-ui/components/**/*.{js,jsx,ts,tsx}',
    '../../packages/common/lib/**/*.{js,jsx}',
    '../../packages/common/lib-src/**/*.{ts,tsx}',
  ],
  darkMode: 'class',
  theme: {
    fontFamily: {
      sans: ['"NVIDIA Sans"', 'system-ui', '-apple-system', 'sans-serif'],
      mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
    },
    borderRadius: {
      'none': '0',
      'sm': '0.0625rem',
      DEFAULT: '0.125rem',
      'md': '0.1875rem',
      'lg': '0.25rem',
      'xl': '0.375rem',
      '2xl': '0.5rem',
      '3xl': '0.75rem',
      'full': '9999px',
    },
    extend: {
      colors: {
        gray: {
          750: '#2d3748',
        },
        'brand-green': '#76b900',
        'brand-green-dark': '#5a8d00',
      },
      screens: {
        xs: '320px',
        sm: '344px',
        base: '768px',
        md: '960px',
        lg: '1280px',
        xl: '1440px',
        xxl: '1600px',
      },
      fontSize: {
        xs: ['0.6rem', { lineHeight: '1rem' }],
        sm: ['0.875rem', { lineHeight: '1.25rem' }],
        base: ['0.9rem', { lineHeight: '1.5rem' }],
        md: ['1.0rem', { lineHeight: '1.5rem' }],
        lg: ['1.125rem', { lineHeight: '1.75rem' }],
        xl: ['1.25rem', { lineHeight: '1.75rem' }],
      },
    },
  },
  variants: {
    extend: {
      visibility: ['group-hover'],
    },
  },
  plugins: [],
};
