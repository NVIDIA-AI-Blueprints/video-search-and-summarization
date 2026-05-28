// SPDX-License-Identifier: MIT
import { Toaster } from 'react-hot-toast';
import { QueryClient, QueryClientProvider } from 'react-query';
import { appWithTranslation } from 'next-i18next';
import type { AppProps } from 'next/app';
import Head from 'next/head';
import { APPLICATION_TITLE } from '../constants/constants';
import '../styles/globals.css';
import 'rsuite/dist/rsuite.min.css';
import '../styles/rsuite-custom.css';

function App({ Component, pageProps }: AppProps<{}>) {
  const queryClient = new QueryClient();

  return (
    <div>
      <Head>
        <title>{APPLICATION_TITLE}</title>
      </Head>
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