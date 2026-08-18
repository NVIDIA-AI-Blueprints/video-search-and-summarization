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
  // Resolved from this app's own public/ rather than a dependency's node_modules,
  // so the locale files ship with the app and survive removing any UI package.
  localePath:
    typeof window === 'undefined'
      ? require('path').resolve('./public/locales')
      : '/public/locales',
};
