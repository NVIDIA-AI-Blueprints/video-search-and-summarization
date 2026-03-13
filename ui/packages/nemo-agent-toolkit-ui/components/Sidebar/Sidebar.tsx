import { ReactNode } from 'react';

import {
  CloseSidebarButton,
  OpenSidebarButton,
} from './components/OpenCloseButton';

import { SidebarInner } from './SidebarInner';

interface Props<T> {
  isOpen: boolean;
  addItemButtonTitle: string;
  side: 'left' | 'right';
  items: T[];
  itemComponent: ReactNode;
  folderComponent: ReactNode;
  footerComponent?: ReactNode;
  searchTerm: string;
  handleSearchTerm: (searchTerm: string) => void;
  toggleOpen: () => void;
  handleCreateItem: () => void;
  handleCreateFolder: () => void;
  handleDrop: (e: any) => void;
  /** When true, folder section is shown even when items is empty. */
  showFolderSection?: boolean;
}

const Sidebar = <T,>({
  isOpen,
  addItemButtonTitle,
  side,
  items,
  itemComponent,
  folderComponent,
  footerComponent,
  searchTerm,
  handleSearchTerm,
  toggleOpen,
  handleCreateItem,
  handleCreateFolder,
  handleDrop,
  showFolderSection = false,
}: Props<T>) => {
  return isOpen ? (
    <div>
      <div
        className={`fixed inset-0 z-40 transition-opacity duration-300 ${
          isOpen ? 'bg-black opacity-70' : 'bg-transparent opacity-0'
        } md:relative md:w-64`}
        onClick={toggleOpen}
      ></div>

      <div className={`fixed top-0 ${side}-0 z-40 flex h-full w-[260px] flex-none transition-all`}>
        <SidebarInner
          addItemButtonTitle={addItemButtonTitle}
          items={items}
          itemComponent={itemComponent}
          folderComponent={folderComponent}
          footerComponent={footerComponent}
          searchTerm={searchTerm}
          handleSearchTerm={handleSearchTerm}
          handleCreateItem={handleCreateItem}
          handleCreateFolder={handleCreateFolder}
          handleDrop={handleDrop}
          enableDragDrop={true}
          showFolderSection={showFolderSection}
        />
      </div>

      <CloseSidebarButton onClick={toggleOpen} side={side} />
    </div>
  ) : (
    <OpenSidebarButton onClick={toggleOpen} side={side} />
  );
};

export default Sidebar;
