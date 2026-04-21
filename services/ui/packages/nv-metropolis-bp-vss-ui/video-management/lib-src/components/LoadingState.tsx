// SPDX-License-Identifier: MIT
import React from 'react';

type LoadingStateProps = Record<string, never>;

export const LoadingState: React.FC<LoadingStateProps> = () => {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center">
        <div className="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-green-500 mb-4" />
        <p className="text-gray-600 dark:text-gray-400">Loading streams...</p>
      </div>
    </div>
  );
};
