// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import PromptWorkspace from '../../components/PromptWorkspace';

const stream = (id: string, enabled: boolean) => ({ id, revision: 1, config: { enabled, interval_seconds: 2, question: { type: 'noul', instructions: `Question for ${id}`, criteria: null } } });
const rule = (id: string, streamId: string, enabled: boolean) => ({ id, revision: 1, config: { stream_id: streamId, config_revision: 1, enabled, review_prompt: `Review for ${streamId}`, field: 'noul', value: 0.8, operator: 'gte', consecutive_samples: 3, pre_seconds: 10, post_seconds: 5, cooldown_seconds: 60, max_gap_seconds: 10 } });
const warehouse = stream('recorded-warehouse', false);
const live = stream('traffic-caltrans-live', true);
const recorded = stream('traffic-caltrans-recorded', false);
const rules = [rule('vest-visible', warehouse.id, false), rule('traffic-live', live.id, true), rule('traffic-recorded', recorded.id, false)];
const props = { streams: [warehouse, live, recorded], rules, ready: true, onApplied: jest.fn() };
const openPrompts = () => fireEvent.click(screen.getByRole('tab', { name: /Review prompts/ }));
const openConversation = () => fireEvent.click(screen.getByRole('tab', { name: 'Conversation' }));
const reply = (value: unknown, ok = true) => ({ ok, json: async () => value });

describe('Persistent prompt workspace', () => {
  const originalClone = global.structuredClone;
  const originalFetch = global.fetch;
  beforeEach(() => {
    sessionStorage.clear();
    global.structuredClone = value => JSON.parse(JSON.stringify(value));
  });
  afterEach(() => { global.structuredClone = originalClone; global.fetch = originalFetch; });

  it('shows the prompt assistant purpose and conversation by default', () => {
    const delMar = stream('traffic-delmar-recorded', true);
    render(<PromptWorkspace {...props} streams={[delMar]} rules={[rule('delmar-congestion', delMar.id, true)]} sourceLabel="Del Mar Heights · Recorded backup" />);
    expect(screen.getByRole('region', { name: 'Demo assistant' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Assistant', exact: true })).toBeVisible();
    expect(screen.getByText('Nemotron · prompt configuration')).toBeVisible();
    expect(screen.getByRole('log', { name: 'Chat messages' })).toBeVisible();
    expect(screen.getByLabelText('Describe your monitoring goal')).toBeVisible();
    expect(screen.getByText('For Del Mar Heights · Recorded backup')).toBeVisible();
    expect(screen.getByRole('tab', { name: /Review prompts/ })).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('button', { name: 'Apply both' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Apply both' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Alert on stopped or crawling traffic' }));
    expect(screen.getByLabelText('Describe your monitoring goal')).toHaveValue('Alert on stopped or crawling traffic');
    expect(screen.getByLabelText('Describe your monitoring goal')).toHaveFocus();
    expect(screen.getByRole('button', { name: 'Have NVIDIA Cosmos compare the lanes' })).toBeVisible();
  });

  it('prevents applying a Yes threshold that cannot be verified', async () => {
    global.fetch = jest.fn() as any;
    render(<PromptWorkspace {...props} />);
    openPrompts();
    expect(within(screen.getByLabelText('Trigger field')).queryByRole('option', {name: 'confidence'})).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Threshold / choice key'), {target: {value: '0.4'}});
    fireEvent.click(screen.getByRole('button', {name: 'Apply both'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Yes requires a threshold from 0.5 to 1');
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('loads and explicitly applies the matching stream and rule, retaining the new revision before polling catches up', async () => {
    global.fetch = jest.fn().mockResolvedValue(reply({ stream: { revision: 2 }, rule: { revision: 2 } })) as unknown as typeof fetch;
    render(<PromptWorkspace {...props} />);
    openPrompts();
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue(`Question for ${live.id}`);
    expect(screen.getByLabelText('NVIDIA Cosmos prompt')).toHaveValue(`Review for ${live.id}`);
    fireEvent.change(screen.getByLabelText('D1 decision model prompt'), { target: { value: 'Edited traffic question' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply both' }));
    await screen.findByText('Applied to the D1 decision model and NVIDIA Cosmos.');
    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body);
    expect(body).toMatchObject({ stream_id: live.id, rule_id: 'traffic-live', stream_revision: 1, rule_revision: 1, draft: { question: { instructions: 'Edited traffic question' } } });
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue('Edited traffic question');
    expect(screen.getByRole('button', { name: 'Apply both' })).toBeDisabled();
    const saved = JSON.parse(sessionStorage.getItem(`d1-prompt-workspace-v1:${live.id}`)!);
    expect(saved.base.stream_revision).toBe(2);
  });

  it('opens generated prompts for review without applying automatically or replacing the chat', async () => {
    const generated = { question: { ...live.config.question, instructions: 'Are vehicles stopped or crawling?' }, interval_seconds: 2, review: { ...rules[1].config, review_prompt: 'Compare movement in each lane.' } };
    global.fetch = jest.fn().mockResolvedValue(reply({ reply: 'Here are prompts for sustained congestion.', draft: generated })) as unknown as typeof fetch;
    render(<PromptWorkspace {...props} />);
    const composer = screen.getByLabelText('Describe your monitoring goal');
    fireEvent.change(composer, { target: { value: 'Watch for stopped traffic' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('Draft ready. Review the prompts, then apply both.');
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect((global.fetch as jest.Mock).mock.calls[0][0]).toBe('/api/proxy/d1/assistant/draft');
    expect(JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body).stream_id).toBe(live.id);
    expect(screen.getByRole('tab', { name: /Review prompts/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue('Are vehicles stopped or crawling?');
    expect(screen.getByRole('button', { name: 'Apply both' })).toBeEnabled();
    expect(screen.getByLabelText('Describe your monitoring goal')).toBe(composer);
    expect(screen.getByText('Here are prompts for sustained congestion.')).not.toBeVisible();
    expect(screen.getByLabelText('NVIDIA Cosmos prompt')).toBeVisible();
    openConversation();
    expect(screen.getByText('Here are prompts for sustained congestion.')).toBeVisible();
    expect(screen.getByLabelText('Describe your monitoring goal')).toBe(composer);
    expect(screen.getByRole('button', { name: 'Apply both' })).toBeVisible();
  });

  it('refreshes clean settings when the same source changes while preserving chat and composer text', async () => {
    global.fetch = jest.fn().mockResolvedValue(reply({ reply: 'I can help configure that camera.' })) as unknown as typeof fetch;
    const { rerender } = render(<PromptWorkspace {...props} />);
    fireEvent.change(screen.getByLabelText('Describe your monitoring goal'), { target: { value: 'What can you do?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('I can help configure that camera.');
    await waitFor(() => expect(screen.getByLabelText('Describe your monitoring goal')).toBeEnabled());
    fireEvent.change(screen.getByLabelText('Describe your monitoring goal'), { target: { value: 'A follow-up request' } });
    const updatedStreams = props.streams.map(item => item.id === live.id ? { ...item, revision: 2, config: { ...item.config, question: { ...item.config.question, instructions: 'New active question' } } } : item);
    const updatedRules = rules.map(item => item.id === 'traffic-live' ? { ...item, revision: 2, config: { ...item.config, review_prompt: 'New active review' } } : item);
    rerender(<PromptWorkspace {...props} streams={updatedStreams} rules={updatedRules} />);
    openPrompts();
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue('New active question');
    expect(screen.getByLabelText('NVIDIA Cosmos prompt')).toHaveValue('New active review');
    expect(screen.getByLabelText('Describe your monitoring goal')).toHaveValue('A follow-up request');
    openConversation();
    expect(screen.getByText('I can help configure that camera.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Apply both' })).toBeDisabled();
  });

  it('preserves operator edits across configuration polls', () => {
    const { rerender } = render(<PromptWorkspace {...props} />);
    openPrompts();
    fireEvent.change(screen.getByLabelText('D1 decision model prompt'), { target: { value: 'Unsaved operator draft' } });
    rerender(<PromptWorkspace {...props} streams={props.streams.map(item => ({ ...item, revision: 2 }))} />);
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue('Unsaved operator draft');
  });

  it('preserves a dirty draft on source switch and disables both Send and Apply until the current source is loaded', () => {
    const { rerender } = render(<PromptWorkspace {...props} streamId={live.id} />);
    openPrompts();
    fireEvent.change(screen.getByLabelText('D1 decision model prompt'), { target: { value: 'Keep this live draft' } });
    fireEvent.change(screen.getByLabelText('Describe your monitoring goal'), { target: { value: 'A live camera follow-up' } });
    rerender(<PromptWorkspace {...props} streamId={recorded.id} />);
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue('Keep this live draft');
    expect(screen.getByText(/Your previous source’s draft is preserved/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Apply both' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Load active settings' }));
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue(`Question for ${recorded.id}`);
    expect(screen.getByLabelText('Describe your monitoring goal')).toHaveValue('');
    expect(JSON.parse(sessionStorage.getItem(`d1-prompt-workspace-v1:${live.id}`)!).draft.question.instructions).toBe('Keep this live draft');
  });

  it('does not silently move an unsent chat request to a different source', () => {
    const { rerender } = render(<PromptWorkspace {...props} streamId={live.id} />);
    fireEvent.change(screen.getByLabelText('Describe your monitoring goal'), { target: { value: 'Watch this specific camera' } });
    rerender(<PromptWorkspace {...props} streamId={recorded.id} />);
    expect(screen.getByLabelText('Describe your monitoring goal')).toHaveValue('Watch this specific camera');
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    expect(screen.getByText(/The camera source changed/)).toBeVisible();
  });

  it('follows a source switch automatically when there are no edits', () => {
    const { rerender } = render(<PromptWorkspace {...props} streamId={live.id} />);
    rerender(<PromptWorkspace {...props} streamId={recorded.id} sourceLabel="Recorded backup" />);
    openPrompts();
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue(`Question for ${recorded.id}`);
    expect(screen.getByText('For Recorded backup')).toBeVisible();
  });

  it('reveals invalid controls before native validation focuses a collapsed field', () => {
    global.fetch = jest.fn() as unknown as typeof fetch;
    render(<PromptWorkspace {...props} />);
    openPrompts();
    const interval = screen.getByLabelText('Check every (seconds)');
    const advanced = interval.closest('details')!;
    advanced.open = true;
    fireEvent.change(interval, { target: { value: '0' } });
    advanced.open = false;
    openConversation();
    fireEvent.click(screen.getByRole('button', { name: 'Apply both' }));
    expect(screen.getByRole('tab', { name: /Review prompts/ })).toHaveAttribute('aria-selected', 'true');
    expect(advanced.open).toBe(true);
    expect(interval).toBeVisible();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('retains drafts after a conflict and presents the server error', async () => {
    global.fetch = jest.fn().mockResolvedValue(reply({ error: 'Settings changed. Reload the active settings before applying.' }, false)) as unknown as typeof fetch;
    render(<PromptWorkspace {...props} />);
    openPrompts();
    fireEvent.change(screen.getByLabelText('D1 decision model prompt'), { target: { value: 'Keep my revised question' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply both' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Settings changed.');
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue('Keep my revised question');
    expect(screen.getByRole('button', { name: 'Apply both' })).toBeEnabled();
  });

  it('keeps a legacy warehouse draft without loading it into the active traffic workspace', () => {
    const saved = { draft: { question: warehouse.config.question, interval_seconds: 2, review: rules[0].config }, base: { stream_id: warehouse.id, rule_id: 'vest-visible', stream_revision: 1, rule_revision: 1 }, dirty: true };
    sessionStorage.setItem('d1-prompt-workspace-v1', JSON.stringify(saved));
    render(<PromptWorkspace {...props} />);
    openPrompts();
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue(`Question for ${live.id}`);
    expect(JSON.parse(sessionStorage.getItem(`d1-prompt-workspace-v1:${warehouse.id}`)!)).toEqual(saved);
  });
  it('supports keyboard view switching while preserving the composer and both prompt drafts', () => {
    render(<PromptWorkspace {...props} />);
    const composer = screen.getByLabelText('Describe your monitoring goal');
    fireEvent.change(composer, { target: { value: 'A request in progress' } });
    const conversation = screen.getByRole('tab', { name: 'Conversation' });
    conversation.focus();
    fireEvent.keyDown(conversation, { key: 'ArrowRight' });
    const review = screen.getByRole('tab', { name: /Review prompts/ });
    expect(review).toHaveFocus();
    expect(review).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('D1 decision model prompt')).toBeVisible();
    expect(screen.getByLabelText('NVIDIA Cosmos prompt')).toBeVisible();
    fireEvent.change(screen.getByLabelText('D1 decision model prompt'), { target: { value: 'My D1 decision model draft' } });
    fireEvent.change(screen.getByLabelText('NVIDIA Cosmos prompt'), { target: { value: 'My NVIDIA Cosmos draft' } });
    fireEvent.keyDown(review, { key: 'Home' });
    expect(conversation).toHaveFocus();
    expect(screen.getByLabelText('Describe your monitoring goal')).toBe(composer);
    expect(composer).toHaveValue('A request in progress');
    openPrompts();
    expect(screen.getByLabelText('D1 decision model prompt')).toHaveValue('My D1 decision model draft');
    expect(screen.getByLabelText('NVIDIA Cosmos prompt')).toHaveValue('My NVIDIA Cosmos draft');
  });

});
