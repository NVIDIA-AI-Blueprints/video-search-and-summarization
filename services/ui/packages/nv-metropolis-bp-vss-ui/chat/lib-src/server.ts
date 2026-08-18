// SPDX-License-Identifier: MIT
/**
 * Node-runtime server helpers.
 *
 * Imports next-i18next, which reaches `node:fs`, so this module must not be
 * pulled into an edge route. The chat proxy lives in `./api` for that reason.
 */
import type { GetServerSideProps } from 'next';
import { serverSideTranslations } from 'next-i18next/serverSideTranslations';

/** i18n namespaces the chat renders from. */
const CHAT_NAMESPACES = ['common', 'chat', 'sidebar', 'markdown', 'promptbar', 'settings'];

/**
 * Base `getServerSideProps` for a page hosting the chat. Supplies the i18n
 * payload; hosting pages merge their own data on top.
 */
export const getChatServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    defaultModelId: process.env.DEFAULT_MODEL || '',
    ...(await serverSideTranslations(locale ?? 'en', CHAT_NAMESPACES)),
  },
});
