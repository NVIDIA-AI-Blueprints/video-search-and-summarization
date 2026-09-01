// SPDX-License-Identifier: MIT
import {
  fixMalformedHtml,
  handleIncompleteAgentThinkStepTags,
  handleIncompleteAgentThinkTags,
  replaceMalformedHtmlImages,
  replaceMalformedHtmlVideos,
  replaceMalformedMarkdownImages,
} from '../lib-src/markdown/streaming';

describe('half-written media', () => {
  it('swaps an unfinished markdown image for a placeholder', () => {
    expect(replaceMalformedMarkdownImages('here: ![frame](https://vst/fra')).toContain(
      'src="loading"',
    );
  });

  it('leaves a finished markdown image alone', () => {
    const done = 'here: ![frame](https://vst/frame.jpg)';
    expect(replaceMalformedMarkdownImages(done)).toBe(done);
  });

  it('swaps an unfinished <img> and <video>', () => {
    expect(replaceMalformedHtmlImages('<img src="https://vst/f')).toContain('src="loading"');
    expect(replaceMalformedHtmlVideos('<video src="https://vst/c')).toContain('src="loading"');
  });
});

describe('agent-think balancing', () => {
  it('closes an open trace and marks it streaming', () => {
    const out = handleIncompleteAgentThinkTags('<agent-think>thinking');
    expect(out).toContain('data-streaming="true"');
    expect(out.endsWith('</agent-think>')).toBe(true);
  });

  it('leaves a balanced trace untouched', () => {
    const done = '<agent-think>done</agent-think>';
    expect(handleIncompleteAgentThinkTags(done)).toBe(done);
  });

  it('clears a stale streaming marker once the tag closes', () => {
    const out = handleIncompleteAgentThinkTags(
      '<agent-think title="x" data-streaming="true">done</agent-think>',
    );
    expect(out).not.toContain('data-streaming');
  });

  it('marks only the newest of several open steps', () => {
    const out = handleIncompleteAgentThinkStepTags(
      '<agent-think-step>one</agent-think-step><agent-think-step>two',
    );
    expect(out.match(/data-streaming="true"/g)).toHaveLength(1);
    // The marker belongs to the step that is still being written.
    expect(out.indexOf('data-streaming="true"')).toBeGreaterThan(out.indexOf('one'));
  });

  it('does not treat agent-think-step as an unclosed agent-think', () => {
    const balanced = '<agent-think><agent-think-step>a</agent-think-step></agent-think>';
    expect(handleIncompleteAgentThinkTags(balanced)).toBe(balanced);
  });
});

describe('fixMalformedHtml', () => {
  it('applies every repair and never throws', () => {
    const out = fixMalformedHtml('<agent-think>x<img src="http');
    expect(out).toContain('src="loading"');
    expect(out).toContain('</agent-think>');
    expect(fixMalformedHtml('')).toBe('');
  });
});
