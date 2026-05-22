// SPDX-License-Identifier: MIT
import { createVssMainTabChatVideoUploadRegistry } from '../../utils/chatVideoUploadCompleteRegistry';
import { createMainTabChatVideoUploadRegistrars } from '../../utils/mainTabChatVideoUploadRegistrars';

describe('VSS main-tab chat video upload registrars', () => {
  it('routes upload complete to tabs that registered listeners', () => {
    const registry = createVssMainTabChatVideoUploadRegistry();
    const registrars = createMainTabChatVideoUploadRegistrars(registry);
    const videoCalls: number[] = [];
    const searchCalls: number[] = [];

    registrars['video-management'](() => videoCalls.push(1));
    registrars.search(() => searchCalls.push(1));

    registry.emit({ results: [] });

    expect(videoCalls).toHaveLength(1);
    expect(searchCalls).toHaveLength(1);
  });

  it('does not notify tabs without a registered listener', () => {
    const registry = createVssMainTabChatVideoUploadRegistry();
    const registrars = createMainTabChatVideoUploadRegistrars(registry);
    const mapCalls: number[] = [];

    registrars.map(() => mapCalls.push(1));
    registry.emit({ results: [] });

    expect(mapCalls).toHaveLength(1);
  });
});
