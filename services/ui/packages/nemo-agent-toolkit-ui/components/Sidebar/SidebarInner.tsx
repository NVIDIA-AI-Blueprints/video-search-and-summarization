import { IconFolderPlus, IconMistOff, IconPlus } from '@tabler/icons-react';
import { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import Search from '../Search';

interface SidebarInnerProps<T> {
  addItemButtonTitle: string;
  items: T[];
  itemComponent: ReactNode;
  folderComponent: ReactNode;
  footerComponent?: ReactNode;
  searchTerm: string;
  handleSearchTerm: (searchTerm: string) => void;
  handleCreateItem: () => void;
  handleCreateFolder: () => void;
  handleDrop?: (e: any) => void;
  enableDragDrop?: boolean;
  /** When true, the folder section is shown even when items is empty (e.g. so new folders appear on fresh launch). */
  showFolderSection?: boolean;
}

/**
 * Inner content component for sidebars.
 * Contains the layout structure without positioning/overlay logic.
 * Used by both Sidebar (with positioning) and ChatSidebarContent (embedded).
 */
export const SidebarInner = <T,>({
  addItemButtonTitle,
  items,
  itemComponent,
  folderComponent,
  footerComponent,
  searchTerm,
  handleSearchTerm,
  handleCreateItem,
  handleCreateFolder,
  handleDrop,
  enableDragDrop = true,
  showFolderSection = false,
}: SidebarInnerProps<T>) => {
  const { t } = useTranslation('promptbar');

  const allowDrop = (e: any) => {
    e.preventDefault();
  };

  const highlightDrop = (e: any) => {
    const isDark = document.documentElement.classList.contains('dark');
    e.target.style.background = isDark ? '#171717' : '#e5e7eb';
  };

  const removeHighlight = (e: any) => {
    e.target.style.background = 'none';
  };

  return (
    <div className="flex h-full w-full flex-col space-y-2 bg-gray-50 dark:bg-neutral-900 p-2 text-[14px]">
      <div className="flex items-center">
        <button
          className="text-sidebar flex w-[190px] flex-shrink-0 cursor-pointer select-none items-center gap-3 rounded-md border border-gray-300 dark:border-white/20 p-3 text-gray-900 dark:text-white transition-colors duration-200 hover:bg-gray-200 dark:hover:bg-gray-500/10"
          onClick={() => {
            handleCreateItem();
            handleSearchTerm('');
          }}
        >
          <IconPlus size={16} />
          {addItemButtonTitle}
        </button>

        <button
          className="ml-2 flex flex-shrink-0 cursor-pointer items-center gap-3 rounded-md border border-gray-300 dark:border-white/20 p-3 text-sm text-gray-900 dark:text-white transition-colors duration-200 hover:bg-gray-200 dark:hover:bg-gray-500/10"
          onClick={handleCreateFolder}
        >
          <IconFolderPlus size={16} />
        </button>
      </div>
      
      <Search
        placeholder={t('Search...') || ''}
        searchTerm={searchTerm}
        onSearch={handleSearchTerm}
      />

      <div className="flex-grow overflow-auto">
        {(items?.length > 0 || showFolderSection) && (
          <div className="flex border-b border-gray-300 dark:border-white/20 pb-2">
            {folderComponent}
          </div>
        )}

        {items?.length > 0 ? (
          <div
            className="pt-2"
            onDrop={enableDragDrop && handleDrop ? handleDrop : undefined}
            onDragOver={enableDragDrop ? allowDrop : undefined}
            onDragEnter={enableDragDrop ? highlightDrop : undefined}
            onDragLeave={enableDragDrop ? removeHighlight : undefined}
          >
            {itemComponent}
          </div>
        ) : (
          <div className="mt-8 select-none text-center text-gray-500 dark:text-white opacity-50">
            <IconMistOff className="mx-auto mb-3" />
            <span className="text-[14px] leading-normal">
              {t('No data.')}
            </span>
          </div>
        )}
      </div>
      
      {footerComponent}
    </div>
  );
};

