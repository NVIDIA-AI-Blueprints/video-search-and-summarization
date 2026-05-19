import {
  countAllowedUploadHiddenPrompts,
  shouldAllowChatMessageSend,
  shouldSendUploadHiddenMessage,
  stripUploadConversationScope,
} from '@/utils/uploadHiddenMessage';

describe('shouldSendUploadHiddenMessage', () => {
  it('allows hidden messages without upload scope', () => {
    expect(shouldSendUploadHiddenMessage(undefined, 'conv-a')).toBe(true);
    expect(shouldSendUploadHiddenMessage(undefined, undefined)).toBe(true);
  });

  it('requires matching conversation id when upload scope is set', () => {
    expect(shouldSendUploadHiddenMessage('conv-a', 'conv-a')).toBe(true);
    expect(shouldSendUploadHiddenMessage('conv-a', 'conv-b')).toBe(false);
    expect(shouldSendUploadHiddenMessage('conv-a', undefined)).toBe(false);
  });
});

describe('shouldAllowChatMessageSend', () => {
  const convA = 'conv-a';

  it('blocks normal chat while an upload dialog is open', () => {
    expect(
      shouldAllowChatMessageSend({
        hidden: false,
        uploadFlowActive: true,
        activeConversationId: convA,
      }),
    ).toBe(false);
  });

  it('allows hidden upload prompts while upload flow is active if conversation matches', () => {
    expect(
      shouldAllowChatMessageSend({
        hidden: true,
        uploadConversationId: convA,
        uploadFlowActive: true,
        activeConversationId: convA,
      }),
    ).toBe(true);
  });

  it('blocks stale hidden prompts after conversation switch', () => {
    expect(
      shouldAllowChatMessageSend({
        hidden: true,
        uploadConversationId: convA,
        uploadFlowActive: false,
        activeConversationId: 'conv-b',
      }),
    ).toBe(false);
  });
});

describe('stripUploadConversationScope', () => {
  it('removes uploadConversationId before persistence', () => {
    expect(
      stripUploadConversationScope({
        role: 'user',
        content: "Let's show the videos just uploaded foo?",
        hidden: true,
        uploadConversationId: 'conv-a',
      }),
    ).toEqual({
      role: 'user',
      content: "Let's show the videos just uploaded foo?",
      hidden: true,
    });
  });
});

describe('countAllowedUploadHiddenPrompts (sequential uploads)', () => {
  const convA = 'conv-a';
  const convB = 'conv-b';

  it('sends a hidden prompt for each upload when the user stays on the same conversation', () => {
    const sent = countAllowedUploadHiddenPrompts([
      {
        uploadConversationId: convA,
        activeConversationIdAtComplete: convA,
        uploadFlowActiveAtComplete: true,
      },
      {
        uploadConversationId: convA,
        activeConversationIdAtComplete: convA,
        uploadFlowActiveAtComplete: false,
      },
      {
        uploadConversationId: convA,
        activeConversationIdAtComplete: convA,
        uploadFlowActiveAtComplete: false,
      },
    ]);

    expect(sent).toBe(3);
  });

  it('sends the first prompt but drops the second after a mid-session conversation switch', () => {
    const sent = countAllowedUploadHiddenPrompts([
      {
        uploadConversationId: convA,
        activeConversationIdAtComplete: convA,
      },
      {
        uploadConversationId: convA,
        activeConversationIdAtComplete: convB,
      },
    ]);

    expect(sent).toBe(1);
  });

  it('allows a second upload prompt on a new conversation when that batch started there', () => {
    const sent = countAllowedUploadHiddenPrompts([
      {
        uploadConversationId: convA,
        activeConversationIdAtComplete: convA,
      },
      {
        uploadConversationId: convB,
        activeConversationIdAtComplete: convB,
      },
    ]);

    expect(sent).toBe(2);
  });

  it('does not treat agent streaming as a blocker (uploadFlowActive false between batches)', () => {
    // Second batch completes with dialogs closed; user may still be waiting on agent reply.
    const sent = countAllowedUploadHiddenPrompts([
      {
        uploadConversationId: convA,
        activeConversationIdAtComplete: convA,
        uploadFlowActiveAtComplete: true,
      },
      {
        uploadConversationId: convA,
        activeConversationIdAtComplete: convA,
        uploadFlowActiveAtComplete: false,
      },
    ]);

    expect(sent).toBe(2);
  });

  it('blocks only the user-typed message during an in-flight upload, not the auto-prompt', () => {
    const uploadFlowActive = true;

    expect(
      shouldAllowChatMessageSend({
        hidden: false,
        uploadFlowActive,
        activeConversationId: convA,
      }),
    ).toBe(false);

    expect(
      shouldAllowChatMessageSend({
        hidden: true,
        uploadConversationId: convA,
        uploadFlowActive,
        activeConversationId: convA,
      }),
    ).toBe(true);
  });
});
