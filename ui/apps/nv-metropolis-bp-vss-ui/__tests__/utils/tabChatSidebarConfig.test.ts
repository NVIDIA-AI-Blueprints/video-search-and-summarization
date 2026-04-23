// SPDX-License-Identifier: MIT
/// <reference path="../../next-runtime-env-mock.d.ts" />
import {
  getTabEnvKey,
  getTabStorageKeyPrefix,
  getChatSidebarEnabled,
  getChatSidebarOpenDefault,
  getChatSidebarOpenFromSession,
  setChatSidebarOpenInSession,
  getChatSidebarOpenSessionKey,
} from '../../utils/tabChatSidebarConfig';
import { setMockEnv, clearMockEnv } from 'next-runtime-env';

let setItemSpy: jest.SpyInstance | undefined;
let getItemSpy: jest.SpyInstance | undefined;

// In-memory sessionStorage mock (no Storage.prototype calls — jsdom rejects plain objects).
let store: Record<string, string> = {};
const sessionStorageMock = {
  getItem: (key: string) => store[key] ?? null,
  setItem: (key: string, value: string) => {
    store[key] = value;
  },
  clear: () => {
    store = {};
  },
  removeItem: (key: string) => {
    delete store[key];
  },
  get length() {
    return Object.keys(store).length;
  },
  key: (i: number) => Object.keys(store)[i] ?? null,
};

// Do not re-declare global.sessionStorage or global.window via declare global—they are
// already typed (Storage, Window & typeof globalThis). Use a typed cast instead.
interface TestGlobal {
  sessionStorage: typeof sessionStorageMock;
  window?: { sessionStorage: typeof sessionStorageMock };
}
const testGlobal = global as unknown as TestGlobal;

Object.defineProperty(global, 'sessionStorage', { value: sessionStorageMock, writable: true });
const windowWithMock = { sessionStorage: sessionStorageMock } as TestGlobal['window'];
if (typeof testGlobal.window === 'undefined') {
  testGlobal.window = windowWithMock;
} else {
  testGlobal.window!.sessionStorage = sessionStorageMock;
}

beforeEach(() => {
  clearMockEnv();
  store = {};
  setItemSpy = jest.spyOn(sessionStorageMock, 'setItem');
  getItemSpy = jest.spyOn(sessionStorageMock, 'getItem');
});

afterEach(() => {
  setItemSpy?.mockRestore();
  getItemSpy?.mockRestore();
});

describe('getTabEnvKey', () => {
  it.each([
    ['search', 'SEARCH_TAB'],
    ['alerts', 'ALERTS_TAB'],
    ['video-management', 'VIDEO_MANAGEMENT_TAB'],
    ['dashboard', 'DASHBOARD_TAB'],
    ['map', 'MAP_TAB'],
  ])('converts "%s" to "%s"', (input, expected) => {
    expect(getTabEnvKey(input)).toBe(expected);
  });
});

describe('getTabStorageKeyPrefix', () => {
  it.each([
    ['search', 'searchTab'],
    ['alerts', 'alertsTab'],
    ['video-management', 'videoManagementTab'],
    ['dashboard', 'dashboardTab'],
  ])('converts "%s" to camelCase "%s"', (input, expected) => {
    expect(getTabStorageKeyPrefix(input)).toBe(expected);
  });
});

describe('getChatSidebarEnabled', () => {
  it('returns false when env var is not set', () => {
    expect(getChatSidebarEnabled()).toBe(false);
  });

  it('returns true when env var is "true"', () => {
    setMockEnv('NEXT_PUBLIC_ENABLE_CHAT_SIDEBAR', 'true');
    expect(getChatSidebarEnabled()).toBe(true);
  });

  it('returns false when env var is "false"', () => {
    setMockEnv('NEXT_PUBLIC_ENABLE_CHAT_SIDEBAR', 'false');
    expect(getChatSidebarEnabled()).toBe(false);
  });
});

describe('getChatSidebarOpenDefault', () => {
  it('returns false when env var is not set', () => {
    expect(getChatSidebarOpenDefault()).toBe(false);
  });

  it('returns true when env var is "true"', () => {
    setMockEnv('NEXT_PUBLIC_CHAT_SIDEBAR_OPEN_DEFAULT', 'true');
    expect(getChatSidebarOpenDefault()).toBe(true);
  });
});

describe('getChatSidebarOpenSessionKey', () => {
  it('returns fixed app key', () => {
    expect(getChatSidebarOpenSessionKey()).toBe('nvMetropolis_chatSidebarOpen');
  });
});

describe('getChatSidebarOpenFromSession / setChatSidebarOpenInSession', () => {
  it('returns null when nothing is stored', () => {
    expect(getChatSidebarOpenFromSession()).toBe(null);
  });

  it('calls setItem with correct key when open=true', () => {
    setChatSidebarOpenInSession(true);
    expect(setItemSpy).toHaveBeenCalledWith('nvMetropolis_chatSidebarOpen', 'true');
  });

  it('calls setItem with correct key when open=false', () => {
    setChatSidebarOpenInSession(false);
    expect(setItemSpy).toHaveBeenCalledWith('nvMetropolis_chatSidebarOpen', 'false');
  });

  it('reads stored "true" value', () => {
    sessionStorage.setItem('nvMetropolis_chatSidebarOpen', 'true');
    expect(getChatSidebarOpenFromSession()).toBe(true);
  });

  it('reads stored "false" value', () => {
    sessionStorage.setItem('nvMetropolis_chatSidebarOpen', 'false');
    expect(getChatSidebarOpenFromSession()).toBe(false);
  });

  it('returns null for unexpected stored value', () => {
    sessionStorage.setItem('nvMetropolis_chatSidebarOpen', 'maybe');
    expect(getChatSidebarOpenFromSession()).toBe(null);
  });
});
