// Export for embedding the entire Next.js app
import { AppProps } from 'next/app';
import dynamic from 'next/dynamic';

// Export the main App component for when consumers want to use the entire app wrapper
export { default as App } from './pages/_app';

// Export the Home component as the main app entry point
export { default as NemoAgentToolkitApp } from './pages/api/home/home';
export type { NemoAgentToolkitAppProps } from './pages/api/home/home';

// Dynamic import version for embedding (avoids SSR issues)
export const EmbeddedNemoApp = dynamic(
  () => import('./pages/api/home/home'),
  { ssr: false }
);

// App wrapper with all providers for embedding
export const NemoAppWithProviders = dynamic(
  () => import('./pages/_app'),
  { ssr: false }
);
