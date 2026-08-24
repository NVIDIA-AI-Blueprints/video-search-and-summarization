import { FC } from 'react';

interface Props {
  text: string;
  icon: JSX.Element;
  onClick: () => void;
  disabled?: boolean;
  /** Shown on hover; wrapped so tooltips work when the button is disabled. */
  title?: string;
}

export const SidebarButton: FC<Props> = ({
  text,
  icon,
  onClick,
  disabled = false,
  title,
}) => {
  const button = (
    <button
      type="button"
      className={`flex w-full select-none items-center gap-3 rounded-md py-3 px-3 text-[14px] leading-3 transition-colors duration-200 ${
        disabled
          ? 'pointer-events-none cursor-not-allowed text-gray-400 dark:text-gray-600'
          : 'cursor-pointer text-gray-900 dark:text-white hover:bg-gray-200 dark:hover:bg-gray-500/10'
      }`}
      onClick={onClick}
      disabled={disabled}
    >
      <div>{icon}</div>
      <span>{text}</span>
    </button>
  );

  // Wrapper receives hover (disabled buttons do not); cursor must live on the wrapper.
  if (disabled) {
    return (
      <span
        className={`flex w-full ${title ? 'cursor-not-allowed' : 'cursor-default'}`}
        title={title}
      >
        {button}
      </span>
    );
  }

  return button;
};
