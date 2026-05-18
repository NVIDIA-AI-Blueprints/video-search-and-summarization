import { IconPlus } from '@tabler/icons-react';
import { FC } from 'react';

import { Conversation } from '@/types/chat';

interface Props {
  selectedConversation: Conversation;
  onNewConversation: () => void;
  newConversationDisabled?: boolean;
  newConversationDisabledTitle?: string;
}

export const Navbar: FC<Props> = ({
  selectedConversation,
  onNewConversation,
  newConversationDisabled = false,
  newConversationDisabledTitle,
}) => {
  return (
    <nav className="flex w-full justify-between bg-black py-3 px-4">
      <div className="mr-4"></div>

      <div className="max-w-[240px] overflow-hidden text-ellipsis whitespace-nowrap">
        {selectedConversation.name}
      </div>

      <IconPlus
        className={`mr-8 ${
          newConversationDisabled
            ? 'cursor-not-allowed opacity-50'
            : 'cursor-pointer hover:text-neutral-400'
        }`}
        title={newConversationDisabled ? newConversationDisabledTitle : undefined}
        onClick={() => {
          if (!newConversationDisabled) onNewConversation();
        }}
      />
    </nav>
  );
};
