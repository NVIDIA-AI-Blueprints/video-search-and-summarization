// SPDX-License-Identifier: MIT
import { DocumentProps, Head, Html, Main, NextScript } from 'next/document';
import Script from 'next/script';

import i18nextConfig from '../next-i18next.config';
import { APPLICATION_TITLE } from '../constants/constants';

type Props = DocumentProps & {
  // add custom document props
};

export default function Document(props: Props) {
  const currentLocale =
    props.__NEXT_DATA__.locale ?? i18nextConfig.i18n.defaultLocale;
  return (
    <Html lang={currentLocale}>
      <Head>
        <title>{APPLICATION_TITLE}</title>
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta
          name="apple-mobile-web-app-title"
          content={APPLICATION_TITLE}
        />
        <link rel="icon" type="image/jpeg" href="/favicon.jpg" />
      </Head>
      <body>
        <Script src="/__ENV.js" strategy="beforeInteractive" />
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}