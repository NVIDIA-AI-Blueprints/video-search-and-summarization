import { FolderInterface } from '@/types/folder';

const key = (base: string, prefix?: string | null) =>
  prefix ? `${prefix}_${base}` : base;

export const saveFolders = (
  folders: FolderInterface[],
  storageKeyPrefix?: string | null,
) => {
  sessionStorage.setItem(
    key('folders', storageKeyPrefix),
    JSON.stringify(folders),
  );
};
