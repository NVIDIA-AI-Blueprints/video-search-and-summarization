import {
  isActiveConversationProcessing,
  isConversationQueryInFlight,
  isFolderDeleteBlocked,
  isQueryProcessing,
} from '@/utils/app/queryProcessing';

describe('isQueryProcessing', () => {
  it('returns false when neither loading nor streaming', () => {
    expect(isQueryProcessing(false, false)).toBe(false);
  });

  it('returns true when loading', () => {
    expect(isQueryProcessing(true, false)).toBe(true);
  });

  it('returns true when streaming', () => {
    expect(isQueryProcessing(false, true)).toBe(true);
  });

  it('returns true when both loading and streaming', () => {
    expect(isQueryProcessing(true, true)).toBe(true);
  });
});

describe('isActiveConversationProcessing', () => {
  it('returns false when conversation is not selected', () => {
    expect(isActiveConversationProcessing('a', 'b', true, true)).toBe(false);
  });

  it('returns false when selected but not processing', () => {
    expect(isActiveConversationProcessing('a', 'a', false, false)).toBe(false);
  });

  it('returns true when selected conversation is processing', () => {
    expect(isActiveConversationProcessing('a', 'a', true, false)).toBe(true);
    expect(isActiveConversationProcessing('a', 'a', false, true)).toBe(true);
  });
});

describe('isConversationQueryInFlight', () => {
  it('returns true when isQueryInFlight is set on conversation', () => {
    expect(
      isConversationQueryInFlight(
        { id: 'a', folderId: 'f1', isQueryInFlight: true },
        'b',
        false,
        false,
      ),
    ).toBe(true);
  });
});

describe('isFolderDeleteBlocked', () => {
  const conversations = [
    { id: 'c1', folderId: 'f1' },
    { id: 'c2', folderId: 'f1' },
    { id: 'c3', folderId: 'f2' },
  ];

  it('returns false when no conversation in folder is processing', () => {
    expect(isFolderDeleteBlocked('f1', conversations, 'c3', false, false)).toBe(
      false,
    );
  });

  it('returns true when selected processing conversation is in folder', () => {
    expect(isFolderDeleteBlocked('f1', conversations, 'c1', true, false)).toBe(
      true,
    );
  });

  it('returns true when non-selected conversation has isQueryInFlight in folder', () => {
    const withBackground = [
      ...conversations,
      { id: 'c-bg', folderId: 'f2', isQueryInFlight: true },
    ];
    expect(
      isFolderDeleteBlocked('f2', withBackground, 'c1', false, false),
    ).toBe(true);
  });

  it('returns false for other folders while one folder has processing', () => {
    expect(isFolderDeleteBlocked('f2', conversations, 'c1', true, false)).toBe(
      false,
    );
  });
});
