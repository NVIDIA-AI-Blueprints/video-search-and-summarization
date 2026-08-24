import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';

import { Image } from '@/components/Markdown/Image';
import { downloadImageFromUrl } from '@/utils/media/download';

jest.mock('react-hot-toast', () => ({
  success: jest.fn(),
  error: jest.fn(),
}));

jest.mock('@/utils/media/download', () => ({
  downloadImageFromUrl: jest.fn().mockResolvedValue(undefined),
}));

const SRC = 'https://example.com/snapshot.jpg';
const ALT = 'Camera snapshot';

function renderLoadedImage(
  props: Partial<React.ComponentProps<typeof Image>> = {},
) {
  render(<Image src={SRC} alt={ALT} {...props} />);
  const inlineImage = screen.getByRole('img', { name: ALT });
  fireEvent.load(inlineImage);
  return inlineImage;
}

function getFullscreenDialog() {
  return within(document.body).getByRole('dialog', { name: ALT });
}

describe('Markdown Image fullscreen', () => {
  beforeEach(() => {
    document.body.style.overflow = '';
    jest.clearAllMocks();
  });

  it('does not open fullscreen before the image has loaded', () => {
    render(<Image src={SRC} alt={ALT} />);

    fireEvent.click(screen.getByRole('img', { name: ALT }));

    expect(within(document.body).queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens a fullscreen overlay portaled to document.body when the inline image is clicked', () => {
    const inlineImage = renderLoadedImage();

    fireEvent.click(inlineImage);

    const dialog = getFullscreenDialog();
    expect(dialog).toBeInTheDocument();
    expect(dialog.parentElement).toBe(document.body);
    expect(dialog).toHaveClass('fixed', 'inset-0', 'z-[9999]');
    expect(within(dialog).getAllByRole('img', { name: ALT })).toHaveLength(1);
  });

  it('locks body scroll while fullscreen is open and restores it on close', () => {
    const inlineImage = renderLoadedImage();

    fireEvent.click(inlineImage);
    expect(document.body.style.overflow).toBe('hidden');

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.body.style.overflow).toBe('');
    expect(within(document.body).queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes fullscreen when the close button is clicked', () => {
    const inlineImage = renderLoadedImage();

    fireEvent.click(inlineImage);
    fireEvent.click(screen.getByRole('button', { name: 'Close fullscreen' }));

    expect(within(document.body).queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('downloads from the fullscreen overlay when showDownload is enabled', async () => {
    const inlineImage = renderLoadedImage({ showDownload: true });

    fireEvent.click(inlineImage);
    fireEvent.click(
      within(getFullscreenDialog()).getByRole('button', {
        name: 'Download image',
      }),
    );

    expect(downloadImageFromUrl).toHaveBeenCalledWith(SRC, ALT);
  });
});
