import { IconFileExport, IconSettings } from '@tabler/icons-react';
import { useContext, useState } from 'react';

import { useTranslation } from 'next-i18next';

import HomeContext from '@/pages/api/home/home.context';

import { SettingDialog } from '@/components/Settings/SettingDialog';

import { Import } from '../../Settings/Import';
import { SidebarButton } from '../../Sidebar/SidebarButton';
import ChatbarContext from '../Chatbar.context';
import { ClearConversations } from './ClearConversations';

interface ChatbarSettingsProps {
  // Optional props - if not provided, will use Context
  conversations?: any[];
  onClearConversations?: () => void;
  onImportConversations?: (data: any) => void;
  onExportData?: () => void;
}

export const ChatbarSettings = ({
  conversations: conversationsProp,
  onClearConversations: onClearConversationsProp,
  onImportConversations: onImportConversationsProp,
  onExportData: onExportDataProp,
}: ChatbarSettingsProps = {}) => {
  const { t } = useTranslation('sidebar');
  const [isSettingDialogOpen, setIsSettingDialog] = useState<boolean>(false);

  const homeContext = useContext(HomeContext);
  const chatbarContext = useContext(ChatbarContext);

  // Use props if provided, otherwise fall back to context
  const conversations = conversationsProp ?? homeContext?.state?.conversations ?? [];
  const handleClearConversations = onClearConversationsProp ?? chatbarContext?.handleClearConversations;
  const handleImportConversations = onImportConversationsProp ?? chatbarContext?.handleImportConversations;
  const handleExportData = onExportDataProp ?? chatbarContext?.handleExportData;

  // If neither props nor context available, don't render
  if (!handleClearConversations || !handleImportConversations || !handleExportData) {
    return null;
  }

  return (
    <div className="flex flex-col items-center space-y-1 border-t border-gray-300 dark:border-white/20 pt-1 text-sm">
      {conversations.length > 0 ? (
        <ClearConversations onClearConversations={handleClearConversations} />
      ) : null}

      <Import onImport={handleImportConversations} />

      <SidebarButton
        text={t('Export data')}
        icon={<IconFileExport size={18} />}
        onClick={() => handleExportData()}
      />

      {homeContext && (
        <>
          <SidebarButton
            text={t('Settings')}
            icon={<IconSettings size={18} />}
            onClick={() => setIsSettingDialog(true)}
          />

          <SettingDialog
            open={isSettingDialogOpen}
            onClose={() => {
              setIsSettingDialog(false);
            }}
          />
        </>
      )}
    </div>
  );
};
