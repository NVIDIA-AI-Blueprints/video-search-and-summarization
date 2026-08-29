// SPDX-License-Identifier: MIT
// No NeMo Agent Toolkit mocks here on purpose: this package has no toolkit
// dependency, which is the reason it exists.
//
// Two projects because most of the logic here is pure (parsers, import/export,
// markdown repairs) and runs faster and more honestly without a DOM, while the
// panel itself needs one.
module.exports = {
  projects: [
    {
      displayName: 'logic',
      preset: 'ts-jest',
      testEnvironment: 'node',
      moduleNameMapper: { '\\.(css|less|scss|sass)$': 'identity-obj-proxy' },
      testMatch: ['<rootDir>/__tests__/**/*.test.ts'],
    },
    {
      displayName: 'components',
      preset: 'ts-jest',
      testEnvironment: 'jsdom',
      setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
      moduleNameMapper: { '\\.(css|less|scss|sass)$': 'identity-obj-proxy' },
      testMatch: ['<rootDir>/__tests__/**/*.test.tsx'],
      transform: {
        '^.+\\.(t|j)sx?$': [
          'ts-jest',
          { tsconfig: { jsx: 'react-jsx', esModuleInterop: true, allowJs: true } },
        ],
      },
      // The unified/remark/rehype chain ships ESM only, and its transitive
      // deps (hastscript, parse5, micromark, …) do too — so match the whole
      // ecosystem by substring rather than maintaining an exact package list
      // that breaks every time one of them gains a dependency.
      transformIgnorePatterns: [
        '/node_modules/(?!(.*(react-markdown|remark|rehype|hast|mdast|micromark|unist|unified|vfile|parse5|character-entities|decode-named-character-reference|property-information|space-separated-tokens|comma-separated-tokens|html-void-elements|html-url-attributes|web-namespaces|zwitch|bail|trough|is-plain-obj|stringify-entities|ccount|escape-string-regexp|markdown-table|trim-lines|devlop|longest-streak|estree-util-is-identifier-name|parse-entities|character-reference-invalid|is-alphanumerical|is-decimal|is-hexadecimal)))',
      ],
    },
  ],
};
