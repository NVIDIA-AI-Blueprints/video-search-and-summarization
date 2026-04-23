// SPDX-License-Identifier: MIT
/** Shared test helpers for @aiqtoolkit-ui/common tests */

export const createMockFile = (name = 'test.mp4', size = 1024) =>
  new File(['x'.repeat(size)], name, { type: 'video/mp4' });

export const createFileList = (files: File[]): FileList => {
  const list = { ...files, length: files.length, item: (i: number) => files[i] ?? null };
  return list as FileList;
};

export const mockFetchResponse = (data: object, ok = true, status = 200) =>
  jest.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(data),
  });
