// SPDX-License-Identifier: MIT
module.exports = {
  i18n: {
    defaultLocale: 'en',
    locales: [
      'bn',
      'de',
      'en',
      'es',
      'fr',
      'he',
      'id',
      'it',
      'ja',
      'ko',
      'pl',
      'pt',
      'ru',
      'ro',
      'sv',
      'te',
      'vi',
      'zh',
      'ar',
      'tr',
      'ca',
      'fi',
    ],
  },
  // The app's own locales. This used to resolve into the toolkit package,
  // which shipped 9 English-to-English strings for components that no longer
  // exist; nothing was ever translated, so nothing is lost by pointing here.
  localePath:
    typeof window === 'undefined'
      ? require('path').resolve('./public/locales')
      : '/public/locales',
};
