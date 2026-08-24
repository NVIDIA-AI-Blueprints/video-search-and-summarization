import { MouseEventHandler, ReactElement } from 'react';

interface Props {
  handleClick: MouseEventHandler<HTMLButtonElement>;
  children: ReactElement;
  disabled?: boolean;
  title?: string;
}

const SidebarActionButton = ({
  handleClick,
  children,
  disabled = false,
  title,
}: Props) => (
  <button
    type="button"
    className={`min-w-[20px] p-1 ${
      disabled
        ? 'cursor-not-allowed opacity-50'
        : 'text-gray-500 hover:text-gray-900 dark:text-neutral-400 dark:hover:text-neutral-100'
    }`}
    onClick={handleClick}
    disabled={disabled}
    title={title}
  >
    {children}
  </button>
);

export default SidebarActionButton;
