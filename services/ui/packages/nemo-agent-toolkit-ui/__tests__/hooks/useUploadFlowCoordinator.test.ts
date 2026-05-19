import { act, renderHook } from '@testing-library/react';
import { useUploadFlowCoordinator } from '@/hooks/useUploadFlowCoordinator';

describe('useUploadFlowCoordinator', () => {
  it('tracks active state across multiple upload sources', () => {
    const { result } = renderHook(() => useUploadFlowCoordinator());

    expect(result.current.uploadFlowActive).toBe(false);

    act(() => {
      result.current.reportUploadFlowActive('chat-input', true);
    });
    expect(result.current.uploadFlowActive).toBe(true);
    expect(result.current.uploadFlowActiveRef.current).toBe(true);

    act(() => {
      result.current.reportUploadFlowActive('chat-header', true);
    });
    expect(result.current.uploadFlowActive).toBe(true);

    act(() => {
      result.current.reportUploadFlowActive('chat-input', false);
    });
    expect(result.current.uploadFlowActive).toBe(true);

    act(() => {
      result.current.reportUploadFlowActive('chat-header', false);
    });
    expect(result.current.uploadFlowActive).toBe(false);
    expect(result.current.uploadFlowActiveRef.current).toBe(false);
  });

  it('allows a second upload batch after the first upload flow fully closes', () => {
    const { result } = renderHook(() => useUploadFlowCoordinator());

    act(() => {
      result.current.reportUploadFlowActive('chat-input', true);
    });
    expect(result.current.uploadFlowActive).toBe(true);

    act(() => {
      result.current.reportUploadFlowActive('chat-input', false);
    });
    expect(result.current.uploadFlowActive).toBe(false);

    act(() => {
      result.current.reportUploadFlowActive('chat-input', true);
    });
    expect(result.current.uploadFlowActive).toBe(true);

    act(() => {
      result.current.reportUploadFlowActive('chat-input', false);
    });
    expect(result.current.uploadFlowActive).toBe(false);
  });
});
