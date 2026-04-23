import {
  IconCaretDown,
  IconCaretRight,
  IconCheck,
  IconPlus,
  IconPencil,
  IconTrash,
  IconX,
} from '@tabler/icons-react';
import {
  KeyboardEvent,
  ReactElement,
  useContext,
  useEffect,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';

import { FolderInterface } from '@/types/folder';

import HomeContext from '@/pages/api/home/home.context';

import SidebarActionButton from '@/components/Buttons/SidebarActionButton';

interface Props {
  currentFolder: FolderInterface;
  searchTerm: string;
  handleDrop: (e: any, folder: FolderInterface) => void;
  folderComponent: (ReactElement | undefined)[];
}

const Folder = ({
  currentFolder,
  searchTerm,
  handleDrop,
  folderComponent,
}: Props) => {
  const { t } = useTranslation('sidebar');
  const homeContext = useContext(HomeContext);

  // Guard against undefined context - component might be rendered outside HomeContext.Provider
  if (!homeContext) {
    return null;
  }

  const { state, dispatch, handleDeleteFolder, handleUpdateFolder, handleNewConversation } = homeContext;

  const [isDeleting, setIsDeleting] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [isOpen, setIsOpen] = useState(false);

  const handleEnterDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleRename();
    }
  };

  const handleRename = () => {
    handleUpdateFolder(currentFolder.id, renameValue);
    setRenameValue('');
    setIsRenaming(false);
  };

  const dropHandler = (e: any) => {
    if (e.dataTransfer) {
      setIsOpen(true);

      handleDrop(e, currentFolder);

      e.target.style.background = 'none';
    }
  };

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

  useEffect(() => {
    if (isRenaming) {
      setIsDeleting(false);
    } else if (isDeleting) {
      setIsRenaming(false);
    }
  }, [isRenaming, isDeleting]);

  useEffect(() => {
    if (searchTerm) {
      setIsOpen(true);
    } else {
      setIsOpen(false);
    }
  }, [searchTerm]);

  // Auto-open when a conversation was created in this folder (e.g. via onControlsReady)
  useEffect(() => {
    if (state?.folderIdToExpand === currentFolder.id) {
      setIsOpen(true);
      dispatch({ field: 'folderIdToExpand', value: null });
    }
  }, [state?.folderIdToExpand, currentFolder.id, dispatch]);

  return (
    <>
      <div className="relative flex items-center">
        {isRenaming ? (
          <div className="flex w-full items-center gap-3 bg-gray-200 dark:bg-black/90 p-3 text-gray-900 dark:text-white">
            {isOpen ? (
              <IconCaretDown size={18} />
            ) : (
              <IconCaretRight size={18} />
            )}
            <input
              className="mr-12 flex-1 overflow-hidden overflow-ellipsis border-gray-400 dark:border-neutral-400 bg-transparent text-left text-[12.5px] leading-3 text-gray-900 dark:text-white outline-none focus:border-gray-600 dark:focus:border-neutral-100"
              type="text"
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={handleEnterDown}
              autoFocus
            />
          </div>
        ) : (
          <button
            className={`flex w-full cursor-pointer items-center gap-3 rounded-lg p-3 text-sm text-gray-900 dark:text-white transition-colors duration-200 hover:bg-gray-200 dark:hover:bg-black/90`}
            onClick={() => setIsOpen(!isOpen)}
            onDrop={(e) => dropHandler(e)}
            onDragOver={allowDrop}
            onDragEnter={highlightDrop}
            onDragLeave={removeHighlight}
          >
            {isOpen ? (
              <IconCaretDown size={18} />
            ) : (
              <IconCaretRight size={18} />
            )}

            <div className="relative max-h-5 flex-1 overflow-hidden text-ellipsis whitespace-nowrap break-all text-left text-[12.5px] leading-3 text-gray-900 dark:text-white">
              {currentFolder.name}
            </div>
          </button>
        )}

        {(isDeleting || isRenaming) && (
          <div className="absolute right-1 z-10 flex text-gray-600 dark:text-gray-300">
            <SidebarActionButton
              handleClick={(e) => {
                e.stopPropagation();

                if (isDeleting) {
                  handleDeleteFolder(currentFolder.id);
                } else if (isRenaming) {
                  handleRename();
                }

                setIsDeleting(false);
                setIsRenaming(false);
              }}
            >
              <IconCheck size={18} />
            </SidebarActionButton>
            <SidebarActionButton
              handleClick={(e) => {
                e.stopPropagation();
                setIsDeleting(false);
                setIsRenaming(false);
              }}
            >
              <IconX size={18} />
            </SidebarActionButton>
          </div>
        )}

        {!isDeleting && !isRenaming && (
          <div className="absolute right-1 z-10 flex text-gray-600 dark:text-gray-300">
            <SidebarActionButton
              handleClick={(e) => {
                e.stopPropagation();
                setIsRenaming(true);
                setRenameValue(currentFolder.name);
              }}
            >
              <IconPencil size={18} />
            </SidebarActionButton>
            <SidebarActionButton
              handleClick={(e) => {
                e.stopPropagation();
                setIsDeleting(true);
              }}
            >
              <IconTrash size={18} />
            </SidebarActionButton>
          </div>
        )}
      </div>

      {isOpen ? (
        <>
          <button
            type="button"
            className="ml-5 flex w-full cursor-pointer items-center gap-2 rounded-md border-0 px-2 py-1.5 text-left text-[12.5px] text-gray-600 dark:text-gray-400 transition-colors hover:bg-gray-200 dark:hover:bg-black/90"
            onClick={(e) => {
              e.stopPropagation();
              handleNewConversation(currentFolder.id);
            }}
          >
            <IconPlus size={14} />
            {t('New conversation')}
          </button>
          {folderComponent}
        </>
      ) : null}
    </>
  );
};

export default Folder;
