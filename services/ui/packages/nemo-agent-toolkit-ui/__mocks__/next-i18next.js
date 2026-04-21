/**
 * Mock for next-i18next to avoid ESM transformation issues in Jest
 */

export const useTranslation = (ns) => ({
  t: (key) => key,
  i18n: {
    language: 'en',
    changeLanguage: jest.fn(),
  },
});

export const appWithTranslation = (component) => component;
export const serverSideTranslations = async () => ({ _nextI18Next: {} });