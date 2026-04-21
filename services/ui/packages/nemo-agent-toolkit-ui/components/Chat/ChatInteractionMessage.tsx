'use client';
import { IconInfoCircle, IconX } from '@tabler/icons-react';
import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { toast } from 'react-hot-toast';


export const InteractionModal = ({
  isOpen,
  interactionMessage,
  onClose,
  onSubmit,
  /** When false, hide the Cancel button. Default: true. */
  showCancelButton = true,
}) => {
  if (!isOpen || !interactionMessage) return null;

  const { content } = interactionMessage;
  const [userInput, setUserInput] = useState('');
  const [error, setError] = useState('');

  // Validation for Text Input
  const handleTextSubmit = () => {
    if (content?.required && !userInput.trim()) {
      setError('This field is required.');
      return;
    }
    setError('');
    onSubmit({ interactionMessage, userResponse: userInput });
    onClose();
  };

  // Handle Choice Selection
  const handleChoiceSubmit = (option = '') => {
    if (content?.required && !option) {
      setError('Please select an option.');
      return;
    }
    setError('');
    onSubmit({ interactionMessage, userResponse: option });
    onClose();
  };

  // Handle Radio Selection
  const handleRadioSubmit = () => {
    if (content?.required && !userInput) {
      setError('Please select an option.');
      return;
    }
    setError('');
    onSubmit({ interactionMessage, userResponse: userInput });
    onClose();
  };

  if (content.input_type === 'notification') {
    toast.custom(
      (t) => (
        <div
          className={`flex gap-2 items-center justify-evenly bg-white text-slate-800 dark:bg-slate-800 dark:text-slate-100 px-4 py-2 rounded-lg shadow-md ${
            t.visible ? 'animate-fade-in' : 'animate-fade-out'
          }`}
        >
          <IconInfoCircle size={16} className="text-[#76b900]" />
          <span>
            {content?.text || 'No content found for this notification'}
          </span>
          <button
            onClick={() => toast.dismiss(t.id)}
            className="text-slate-800 dark:bg-slate-800 dark:text-slate-100 ml-3 hover:bg-slate-300 rounded-full p-1"
          >
            <IconX size={12} />
          </button>
        </div>
      ),
      {
        position: 'top-right',
        duration: Infinity,
        id: 'notification-toast',
      },
    );
    return null;
  }

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-black bg-opacity-50 z-50">
      <div className="bg-white dark:bg-gray-800 p-6 rounded-lg shadow-lg sm:w-[75%] h-auto">
        <div className="mb-4 text-slate-800 dark:text-white prose prose-base dark:prose-invert max-w-none prose-headings:font-semibold prose-p:my-1 max-h-[60vh] overflow-y-auto">
          <ReactMarkdown>{content?.text || ''}</ReactMarkdown>
        </div>

        {content.input_type === 'text' && (
          <div>
            <textarea
              className="w-full border border-gray-300 dark:border-gray-600 p-2 rounded text-black dark:text-white bg-white dark:bg-gray-700 placeholder-gray-500 dark:placeholder-gray-400"
              placeholder={content?.placeholder}
              value={userInput}
              onChange={(e) => setUserInput(e.target.value)}
            />
            {error && <p className="text-red-500 text-sm mt-2">{error}</p>}
            <div className="flex justify-end mt-4 space-x-2">
              {showCancelButton && (
                <button
                  className="px-4 py-2 bg-gray-500 dark:bg-gray-600 text-white rounded hover:bg-gray-600 dark:hover:bg-gray-500"
                  onClick={onClose}
                >
                  Cancel
                </button>
              )}
              <button
                className="px-4 py-2 bg-[#76b900] text-white rounded hover:bg-[#5a8c00]"
                onClick={handleTextSubmit}
              >
                Submit
              </button>
            </div>
          </div>
        )}

        {content.input_type === 'binary_choice' && (
          <div>
            <div className="flex justify-end mt-4 space-x-2">
              {content.options.map((option) => (
                <button
                  key={option.id}
                  className={`px-4 py-2 ${
                    option?.value?.includes('continue')
                      ? 'bg-[#76b900]'
                      : 'bg-slate-800'
                  } text-white rounded`}
                  onClick={() => handleChoiceSubmit(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {content.input_type === 'radio' && (
          <div>
            <div className="space-y-3">
              {content.options.map((option) => (
                <div key={option.id} className="flex items-center">
                  <input
                    type="radio"
                    id={option.id}
                    name="notification-method"
                    value={option.value}
                    checked={userInput === option.value}
                    onChange={() => setUserInput(option.value)}
                    className="mr-2 text-[#76b900] focus:ring-[#76b900]"
                  />
                  <label htmlFor={option.id} className="flex flex-col">
                    <span className="text-slate-800 dark:text-white">
                      {option.label}
                    </span>
                    {/* {option.description && (
                      <span className="text-sm text-slate-600 dark:text-slate-400">
                        {option?.description}
                      </span>
                    )} */}
                  </label>
                </div>
              ))}
            </div>
            {error && <p className="text-red-500 text-sm mt-2">{error}</p>}
            <div className="flex justify-end mt-4 space-x-2">
              {showCancelButton && (
                <button
                  className="px-4 py-2 bg-gray-500 dark:bg-gray-600 text-white rounded hover:bg-gray-600 dark:hover:bg-gray-500"
                  onClick={onClose}
                >
                  Cancel
                </button>
              )}
              <button
                className="px-4 py-2 bg-[#76b900] text-white rounded hover:bg-[#5a8c00]"
                onClick={handleRadioSubmit}
              >
                Submit
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
