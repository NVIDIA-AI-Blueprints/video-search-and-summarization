// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { Toaster } from '@/components/ui/sonner';

import './styles.css';

export const metadata: Metadata = {
  title: 'Rise Up!',
  description: 'Video-first incident analysis and reporting.',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-background text-foreground antialiased">
        {children}
        <Toaster position="top-center" />
      </body>
    </html>
  );
}
