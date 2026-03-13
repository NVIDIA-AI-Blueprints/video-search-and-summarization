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
  localePath:
    typeof window === 'undefined'
      ? require('path').resolve('../../node_modules/@nemo-agent-toolkit/ui/lib/public/locales')
      : '/public/locales',
};
