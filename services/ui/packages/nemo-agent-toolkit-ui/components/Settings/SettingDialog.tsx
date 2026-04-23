import { FC, useContext, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';

import { useTranslation } from 'next-i18next';

import HomeContext from '@/pages/api/home/home.context';

interface Props {
  open: boolean;
  onClose: () => void;
}

export const SettingDialog: FC<Props> = ({ open, onClose }) => {
  const { t } = useTranslation('settings');
  const modalRef = useRef<HTMLDivElement>(null);
  const homeContext = useContext(HomeContext);
  
  // Guard against undefined context - component might be rendered outside HomeContext.Provider
  if (!homeContext) {
    return null;
  }
  
  const {
    state: {
      lightMode,
      chatCompletionURL,
      webSocketURL,
      webSocketSchema: schema,
      expandIntermediateSteps,
      intermediateStepOverride,
      enableIntermediateSteps,
      webSocketSchemas,
      themeChangeButtonEnabled,
    },
    dispatch: homeDispatch,
  } = homeContext;

  // Initialize with state values (env-based defaults), sync with sessionStorage in useEffect
  const [theme, setTheme] = useState<'light' | 'dark'>(lightMode);
  const [chatCompletionEndPoint, setChatCompletionEndPoint] = useState(chatCompletionURL);
  const [webSocketEndPoint, setWebSocketEndPoint] = useState(webSocketURL);
  const [webSocketSchema, setWebSocketSchema] = useState(schema);
  const [isIntermediateStepsEnabled, setIsIntermediateStepsEnabled] = useState(enableIntermediateSteps);
  const [detailsToggle, setDetailsToggle] = useState(expandIntermediateSteps);
  const [intermediateStepOverrideToggle, setIntermediateStepOverrideToggle] = useState(intermediateStepOverride);
  const [hasLoadedFromStorage, setHasLoadedFromStorage] = useState(false);

  // Sync with sessionStorage after mount (client-side only)
  // Priority: saved sessionStorage value > current state (which includes env variable fallback)
  useEffect(() => {
    if (typeof window !== 'undefined' && !hasLoadedFromStorage) {
      // Theme
      const savedTheme = sessionStorage.getItem('lightMode');
      if (savedTheme && (savedTheme === 'light' || savedTheme === 'dark')) {
        setTheme(savedTheme as 'light' | 'dark');
      }
      
      // Chat completion URL
      const savedChatUrl = sessionStorage.getItem('chatCompletionURL');
      if (savedChatUrl) {
        setChatCompletionEndPoint(savedChatUrl);
      }
      
      // WebSocket URL
      const savedWsUrl = sessionStorage.getItem('webSocketURL');
      if (savedWsUrl) {
        setWebSocketEndPoint(savedWsUrl);
      }
      
      // WebSocket Schema
      const savedWsSchema = sessionStorage.getItem('webSocketSchema');
      if (savedWsSchema) {
        setWebSocketSchema(savedWsSchema);
      }
      
      // Enable Intermediate Steps
      const savedEnableSteps = sessionStorage.getItem('enableIntermediateSteps');
      if (savedEnableSteps !== null) {
        setIsIntermediateStepsEnabled(savedEnableSteps === 'true');
      }
      
      // Expand Intermediate Steps
      const savedExpandSteps = sessionStorage.getItem('expandIntermediateSteps');
      if (savedExpandSteps !== null) {
        setDetailsToggle(savedExpandSteps === 'true');
      }
      
      // Intermediate Step Override
      const savedOverride = sessionStorage.getItem('intermediateStepOverride');
      if (savedOverride !== null) {
        setIntermediateStepOverrideToggle(savedOverride !== 'false');
      }
      
      setHasLoadedFromStorage(true);
    }
  }, [hasLoadedFromStorage]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (modalRef.current && !modalRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    if (open) {
      window.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      window.removeEventListener('mousedown', handleClickOutside);
    };
  }, [open, onClose]);

  const handleSave = () => {
    if (!chatCompletionEndPoint || !webSocketEndPoint) {
      toast.error('Please fill all the fields to save settings');
      return;
    }

    homeDispatch({ field: 'lightMode', value: theme });
    homeDispatch({ field: 'chatCompletionURL', value: chatCompletionEndPoint });
    homeDispatch({ field: 'webSocketURL', value: webSocketEndPoint });
    homeDispatch({ field: 'webSocketSchema', value: webSocketSchema });
    homeDispatch({ field: 'expandIntermediateSteps', value: detailsToggle });
    homeDispatch({
      field: 'intermediateStepOverride',
      value: intermediateStepOverrideToggle,
    });
    homeDispatch({
      field: 'enableIntermediateSteps',
      value: isIntermediateStepsEnabled,
    });

    // Save theme directly to sessionStorage like other settings
    sessionStorage.setItem('lightMode', theme);
    sessionStorage.setItem('chatCompletionURL', chatCompletionEndPoint);
    sessionStorage.setItem('webSocketURL', webSocketEndPoint);
    sessionStorage.setItem('webSocketSchema', webSocketSchema);
    sessionStorage.setItem('expandIntermediateSteps', String(detailsToggle));
    sessionStorage.setItem(
      'intermediateStepOverride',
      String(intermediateStepOverrideToggle),
    );
    sessionStorage.setItem(
      'enableIntermediateSteps',
      String(isIntermediateStepsEnabled),
    );

    toast.success('Settings saved successfully');
    onClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-black bg-opacity-50 backdrop-blur-sm z-50 dark:bg-opacity-20">
      <div
        ref={modalRef}
        className="w-full max-w-md bg-white dark:bg-black rounded-2xl shadow-lg p-6 transform transition-all relative"
      >
        <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-4">
          {t('Settings')}
        </h2>

        {themeChangeButtonEnabled && (
          <>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('Theme')}
            </label>
            <select
              className="w-full mt-1 p-2 rounded-lg bg-gray-100 dark:bg-black text-gray-900 dark:text-white focus:outline-none"
              value={theme}
              onChange={(e) => setTheme(e.target.value as 'light' | 'dark')}
            >
              <option value="dark">{t('Dark mode')}</option>
              <option value="light">{t('Light mode')}</option>
            </select>
          </>
        )}

        <label className={`block text-sm font-medium text-gray-700 dark:text-gray-300 ${themeChangeButtonEnabled ? 'mt-4' : ''}`}>
          {t('HTTP URL for Chat Completion')}
        </label>
        <input
          type="text"
          value={chatCompletionEndPoint}
          onChange={(e) => setChatCompletionEndPoint(e.target.value)}
          className="w-full mt-1 p-2 rounded-lg bg-gray-100 dark:bg-black text-gray-900 dark:text-white focus:outline-none"
        />

        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mt-4">
          {t('WebSocket URL for Chat Completion')}
        </label>
        <input
          type="text"
          value={webSocketEndPoint}
          onChange={(e) => setWebSocketEndPoint(e.target.value)}
          className="w-full mt-1 p-2 rounded-lg bg-gray-100 dark:bg-black text-gray-900 dark:text-white focus:outline-none"
        />

        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mt-4">
          {t('WebSocket Schema')}
        </label>
        <select
          className="w-full mt-1 p-2 rounded-lg bg-gray-100 dark:bg-black text-gray-900 dark:text-white focus:outline-none"
          value={webSocketSchema}
          onChange={(e) => {
            setWebSocketSchema(e.target.value);
          }}
        >
          {webSocketSchemas?.map((schema) => (
            <option key={schema} value={schema}>
              {schema}
            </option>
          ))}
        </select>

        <div className="flex align-middle text-sm font-medium text-gray-700 dark:text-gray-300 mt-4">
          <input
            type="checkbox"
            id="enableIntermediateSteps"
            checked={isIntermediateStepsEnabled}
            onChange={() => {
              setIsIntermediateStepsEnabled(!isIntermediateStepsEnabled);
            }}
            className="mr-2"
          />
          <label
            htmlFor="enableIntermediateSteps"
            className="text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Enable Intermediate Steps
          </label>
        </div>

        <div className="flex align-middle text-sm font-medium text-gray-700 dark:text-gray-300 mt-4">
          <input
            type="checkbox"
            id="detailsToggle"
            checked={detailsToggle}
            onChange={() => {
              setDetailsToggle(!detailsToggle);
            }}
            disabled={!isIntermediateStepsEnabled}
            className="mr-2"
          />
          <label
            htmlFor="detailsToggle"
            className="text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Expand Intermediate Steps by default
          </label>
        </div>

        <div className="flex align-middle text-sm font-medium text-gray-700 dark:text-gray-300 mt-4">
          <input
            type="checkbox"
            id="intermediateStepOverrideToggle"
            checked={intermediateStepOverrideToggle}
            onChange={() => {
              setIntermediateStepOverrideToggle(
                !intermediateStepOverrideToggle,
              );
            }}
            disabled={!isIntermediateStepsEnabled}
            className="mr-2"
          />
          <label
            htmlFor="intermediateStepOverrideToggle"
            className="text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Override intermediate Steps with same Id
          </label>
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <button
            className="px-4 py-2 bg-gray-300 dark:bg-gray-600 text-gray-900 dark:text-white rounded-md hover:bg-gray-400 dark:hover:bg-gray-500 focus:outline-none"
            onClick={onClose}
          >
            {t('Cancel')}
          </button>
          <button
            className="px-4 py-2 bg-[#76b900] text-white rounded-md hover:bg-[#5a9100] focus:outline-none"
            onClick={handleSave}
          >
            {t('Save')}
          </button>
        </div>
      </div>
    </div>
  );
};
