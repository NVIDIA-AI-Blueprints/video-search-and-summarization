import { useState } from 'react';
import { Toaster } from 'react-hot-toast';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { appWithTranslation } from 'next-i18next/pages';
import type { AppProps } from 'next/app';
import { Inter } from 'next/font/google';

import '@nemo-agent-toolkit/ui/styles';

const inter = Inter({ subsets: ['latin'] });

function App({ Component, pageProps }: AppProps<{}>) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <div className={inter.className}>
      <Toaster
        toastOptions={{
          style: {
            maxWidth: 500,
            wordBreak: 'break-all',
          },
        }}
      />
      <QueryClientProvider client={queryClient}>
        <Component {...pageProps} />
      </QueryClientProvider>
    </div>
  );
}

export default appWithTranslation(App);