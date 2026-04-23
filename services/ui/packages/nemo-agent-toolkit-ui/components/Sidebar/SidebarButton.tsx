import { FC } from 'react';

interface Props {
  text: string;
  icon: JSX.Element;
  onClick: () => void;
  disabled?: boolean;
}

export const SidebarButton: FC<Props> = ({ text, icon, onClick, disabled = false }) => {
  return (
    <button
      className={`flex w-full select-none items-center gap-3 rounded-md py-3 px-3 text-[14px] leading-3 transition-colors duration-200 ${
        disabled
          ? 'cursor-not-allowed text-gray-400 dark:text-gray-600'
          : 'cursor-pointer text-gray-900 dark:text-white hover:bg-gray-200 dark:hover:bg-gray-500/10'
      }`}
      onClick={onClick}
      disabled={disabled}
    >
      <div>{icon}</div>
      <span>{text}</span>
    </button>
  );
};
