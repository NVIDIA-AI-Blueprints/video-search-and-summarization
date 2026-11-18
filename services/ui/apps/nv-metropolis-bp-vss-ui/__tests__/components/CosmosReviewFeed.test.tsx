// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import React, { useState } from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import CosmosReviewFeed from '../../components/CosmosReviewFeed';

jest.mock('../../components/EventQuestions', () => ({ __esModule: true, default: ({ eventId, active = true, busyElsewhere, onPendingChange }: any) => {
  const [draft, setDraft] = useState('');
  return <div data-testid={active ? "question-event" : `inactive-${eventId}`} data-event-id={eventId} data-busy-elsewhere={String(Boolean(busyElsewhere))}><label>Question draft<input value={draft} onChange={event => setDraft(event.target.value)} /></label><button onClick={() => onPendingChange?.(eventId, true)}>Start test answer</button><button onClick={() => onPendingChange?.(eventId, false)}>Finish test answer</button></div>;
} }));

const now = 1794856080;
const candidate = (id: string, timestamp = now, status = 'confirmed') => ({
  id, stream_id:'traffic-recorded', captured_at:timestamp, clip_start:timestamp-15, clip_end:timestamp+5, status, clip_ready:status!=='pending',
  incident:{ id,stream_id:'traffic-recorded',rule_id:'traffic-rule',rule_revision:2,rule:{field:'noul',operator:'gte',value:.85,consecutive_samples:3,pre_seconds:15,post_seconds:5},trigger:{config_revision:4,question:{instructions:'The saved D1 question'}} },
  review:['confirmed','rejected','uncertain'].includes(status)?{event_id:id,verification_version:1,verdict:status,summary:`Review of ${id}`,evidence:['A queue is visible.'],uncertainty:['The cause is outside the view.'],clip_sha256:`sha-${id}`} : null,
});
const props = (events:any[]) => ({events,incidents:[],available:true,autoReview:true,streamRevision:4,activeRule:{id:'traffic-rule',revision:2}});

describe('Cosmos event review feed', () => {
  const originalObserver = global.IntersectionObserver;
  const observers: Array<{ callback: IntersectionObserverCallback; node?: Element; disconnect: jest.Mock }> = [];
  beforeEach(() => {
    observers.length=0;
    global.IntersectionObserver = class {
      item: typeof observers[number];
      constructor(callback: IntersectionObserverCallback) { this.item={callback,disconnect:jest.fn()};observers.push(this.item); }
      observe(node: Element) { this.item.node=node; }
      disconnect() { this.item.disconnect(); }
    } as any;
  });
  afterEach(() => { global.IntersectionObserver=originalObserver; });

  it('pairs newest-first saved clips with their own result and original question', () => {
    render(<CosmosReviewFeed {...props([candidate('older',now-60),candidate('newest')])} />);
    const feed=screen.getByRole('region',{name:'Cosmos review history'});
    const rows=within(feed).getAllByRole('article');
    expect(rows.map(row=>row.getAttribute('data-event-id'))).toEqual(['newest','older']);
    expect(within(rows[0]).getByLabelText('Saved evidence clip newest')).toHaveAttribute('src','/api/d1/clips/newest?attempt=0');
    expect(within(rows[0]).getByText('Review of newest')).toBeInTheDocument();
    fireEvent.click(within(rows[0]).getByText('Evidence & trigger details'));
    expect(within(rows[0]).getByText('The saved D1 question')).toBeInTheDocument();
    expect(within(rows[0]).getByText('D1’s yes score was at least 85% for 3 consecutive checks.')).toBeInTheDocument();
    expect(within(rows[0]).getByText('Reviewed clip SHA-256: sha-newest')).toBeInTheDocument();
    expect(within(rows[0]).getByLabelText('Saved evidence clip newest')).not.toHaveAttribute('autoplay');
  });

  it('loads video metadata only for the first or near-visible saved clip', () => {
    render(<CosmosReviewFeed {...props([candidate('older',now-60),candidate('newest')])} />);
    expect(screen.getByLabelText('Saved evidence clip newest')).toHaveAttribute('preload','metadata');
    expect(screen.queryByLabelText('Saved evidence clip older')).not.toBeInTheDocument();
    const observer=observers[0];
    act(()=>observer.callback([{isIntersecting:true,target:observer.node}] as any,{} as any));
    expect(screen.getByLabelText('Saved evidence clip older')).toHaveAttribute('src','/api/d1/clips/older?attempt=0');
    expect(observer.disconnect).toHaveBeenCalled();
  });

  it('keeps the same video and its playback position when newer clips arrive or a review changes', () => {
    const old=candidate('old');
    const {rerender}=render(<CosmosReviewFeed {...props([old])} />);
    const video=screen.getByLabelText('Saved evidence clip old') as HTMLVideoElement;
    video.currentTime=8;
    rerender(<CosmosReviewFeed {...props([candidate('new',now+60),{...old,review:{...old.review,summary:'Updated review'}}])} />);
    expect(screen.getByLabelText('Saved evidence clip old')).toBe(video);
    expect(video.currentTime).toBe(8);
    expect(screen.getByText('Updated review')).toBeInTheDocument();
  });

  it('retains an actively played clip and open Q&A after they leave the bounded candidate list', () => {
    const old=candidate('old');
    const {rerender}=render(<CosmosReviewFeed {...props([old])} />);
    const video=screen.getByLabelText('Saved evidence clip old') as HTMLVideoElement;
    fireEvent.play(video); video.currentTime=8;
    fireEvent.click(screen.getByRole('button',{name:'Add to chat'}));
    fireEvent.change(screen.getByRole('textbox',{name:'Question draft'}),{target:{value:'What is visible?'}});
    rerender(<CosmosReviewFeed {...props([candidate('new',now+60)])} />);
    expect(screen.getByLabelText('Saved evidence clip old')).toBe(video);
    expect(video.currentTime).toBe(8);
    expect(screen.getByTestId('question-event')).toHaveAttribute('data-event-id','old');
    expect(screen.getByRole('textbox',{name:'Question draft'})).toHaveValue('What is visible?');
  });

  it('retains the latest review rather than the earlier result present when playback started', () => {
    const old={...candidate('old',now,'reviewing'),clip_ready:true};
    const {rerender}=render(<CosmosReviewFeed {...props([old])} />);
    fireEvent.play(screen.getByLabelText('Saved evidence clip old'));
    rerender(<CosmosReviewFeed {...props([candidate('old')])} />);
    rerender(<CosmosReviewFeed {...props([candidate('new',now+60)])} />);
    const row=screen.getAllByRole('article').find(item=>item.getAttribute('data-event-id')==='old')!;
    expect(within(row).getByText('Confirmed',{exact:true})).toBeInTheDocument();
    expect(within(row).getByText('Review of old')).toBeInTheDocument();
  });

  it('anchors an older row when new clips or changing review heights appear above it', () => {
    const old=candidate('old',now-60), recent=candidate('recent');
    const {rerender}=render(<CosmosReviewFeed {...props([recent,old])} />);
    const feed=screen.getByRole('region',{name:'Cosmos review history'});
    const rows=screen.getAllByRole('article');
    jest.spyOn(feed,'getBoundingClientRect').mockReturnValue({top:100,bottom:400} as DOMRect);
    jest.spyOn(rows[0],'getBoundingClientRect').mockReturnValue({top:-200,bottom:90} as DOMRect);
    const position=jest.spyOn(rows[1],'getBoundingClientRect').mockReturnValue({top:90,bottom:390} as DOMRect);
    feed.scrollTop=210; fireEvent.scroll(feed);
    position.mockReturnValue({top:390,bottom:690} as DOMRect);
    rerender(<CosmosReviewFeed {...props([candidate('new',now+60),recent,old])} />);
    expect(feed.scrollTop).toBe(510);
    expect(screen.getByRole('button',{name:'New clips above'})).toBeInTheDocument();
    position.mockReturnValue({top:420,bottom:720} as DOMRect);
    rerender(<CosmosReviewFeed {...props([candidate('new',now+60),{...recent,review:{...recent.review,summary:'Longer updated summary'}},old])} />);
    expect(feed.scrollTop).toBe(540);
  });

  it('shows rejected, uncertain, failed and legacy results without labeling them confirmed', () => {
    const legacy={...candidate('legacy',now-90,'legacy'),review:{event_id:'legacy',context:'An earlier caption'}};
    render(<CosmosReviewFeed {...props([candidate('rejected',now,'rejected'),candidate('uncertain',now-30,'uncertain'),candidate('failed',now-60,'failed'),legacy])} />);
    expect(screen.queryByText('Confirmed',{exact:true})).not.toBeInTheDocument();
    expect(screen.getByText('Not confirmed',{exact:true})).toBeInTheDocument();
    expect(screen.getByText('Uncertain',{exact:true})).toBeInTheDocument();
    expect(screen.getByText('Review failed',{exact:true})).toBeInTheDocument();
    expect(screen.getByText('Verification could not be completed. No verdict was issued.')).toBeInTheDocument();
    expect(screen.getByText('Earlier description · unverified')).toBeInTheDocument();
    expect(screen.getByText('This is a description, not a verification verdict.')).toBeInTheDocument();
  });

  it('shows a permanent empty chat, then preserves its attachment and draft through polling and removal', () => {
    const old=candidate('old');
    const {rerender}=render(<CosmosReviewFeed {...props([old])} />);
    expect(screen.getByRole('complementary',{name:'Chat with Cosmos'})).toBeVisible();
    expect(screen.getByRole('textbox',{name:'Question about attached clip'})).toBeDisabled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Add to chat'}));
    expect(screen.getByTestId('chat-attachment')).toHaveAttribute('data-attached-event-id','old');
    expect(screen.getByRole('button',{name:'In chat'})).toHaveAttribute('aria-pressed','true');
    fireEvent.change(screen.getByRole('textbox',{name:'Question draft'}),{target:{value:'Which lanes are moving?'}});
    rerender(<CosmosReviewFeed {...props([candidate('new',now+30),old])} />);
    expect(screen.getByTestId('chat-attachment')).toHaveAttribute('data-attached-event-id','old');
    expect(screen.getByRole('textbox',{name:'Question draft'})).toHaveValue('Which lanes are moving?');
    fireEvent.click(screen.getByRole('button',{name:'Remove clip from chat'}));
    expect(screen.queryByTestId('chat-attachment')).not.toBeInTheDocument();
    expect(screen.getByRole('button',{name:'Send question'})).toBeDisabled();
    const oldRow=screen.getAllByRole('article').find(row=>row.getAttribute('data-event-id')==='old')!;
    fireEvent.click(within(oldRow).getByRole('button',{name:'Add to chat'}));
    expect(screen.getByRole('textbox',{name:'Question draft'})).toHaveValue('Which lanes are moving?');
  });

  it('switches explicit attachments without mixing or discarding their conversations', () => {
    render(<CosmosReviewFeed {...props([candidate('a'),candidate('b',now-30)])} />);
    const row=(id:string)=>screen.getAllByRole('article').find(item=>item.getAttribute('data-event-id')===id)!;
    fireEvent.click(within(row('a')).getByRole('button',{name:'Add to chat'}));
    fireEvent.change(screen.getByRole('textbox',{name:'Question draft'}),{target:{value:'Question for A'}});
    fireEvent.click(within(row('b')).getByRole('button',{name:'Add to chat'}));
    expect(screen.getByTestId('chat-attachment')).toHaveAttribute('data-attached-event-id','b');
    expect(screen.getByRole('textbox',{name:'Question draft'})).toHaveValue('');
    fireEvent.change(screen.getByRole('textbox',{name:'Question draft'}),{target:{value:'Question for B'}});
    fireEvent.click(within(row('a')).getByRole('button',{name:'Add to chat'}));
    expect(screen.getByRole('textbox',{name:'Question draft'})).toHaveValue('Question for A');
  });

  it('keeps an answering thread alive while another clip is attached and prevents another simultaneous send', () => {
    const events=['a','b','c','d','e'].map((id,i)=>candidate(id,now-i*30));
    render(<CosmosReviewFeed {...props(events)} />);
    const attach=(id:string)=>fireEvent.click(within(screen.getAllByRole('article').find(row=>row.getAttribute('data-event-id')===id)!).getByRole('button',{name:'Add to chat'}));
    attach('a');
    fireEvent.change(screen.getByRole('textbox',{name:'Question draft'}),{target:{value:'Answering A'}});
    fireEvent.click(screen.getByRole('button',{name:'Start test answer'}));
    for(const id of ['b','c','d','e']) attach(id);
    expect(screen.getByTestId('question-event')).toHaveAttribute('data-busy-elsewhere','true');
    expect(screen.getByTestId('inactive-a')).toBeInTheDocument();
    expect(screen.queryByTestId('inactive-b')).not.toBeInTheDocument();
    attach('a');
    expect(screen.getByRole('textbox',{name:'Question draft'})).toHaveValue('Answering A');
    expect(screen.getByTestId('question-event')).toHaveAttribute('data-busy-elsewhere','false');
    fireEvent.click(screen.getByRole('button',{name:'Finish test answer'}));
    attach('e');
    expect(screen.getByTestId('question-event')).toHaveAttribute('data-busy-elsewhere','false');
  });

  it('keeps inactive and detached chat clips reachable after the backend history window advances', () => {
    const a=candidate('a'), b=candidate('b',now+30);
    const {rerender}=render(<CosmosReviewFeed {...props([a,b])} />);
    const attach=(id:string)=>fireEvent.click(within(screen.getAllByRole('article').find(row=>row.getAttribute('data-event-id')===id)!).getByRole('button',{name:'Add to chat'}));
    attach('a');
    fireEvent.change(screen.getByRole('textbox',{name:'Question draft'}),{target:{value:'Keep A reachable'}});
    fireEvent.click(screen.getByRole('button',{name:'Start test answer'}));
    attach('b');
    rerender(<CosmosReviewFeed {...props([b])} />);
    attach('a');
    expect(screen.getByRole('textbox',{name:'Question draft'})).toHaveValue('Keep A reachable');
    fireEvent.click(screen.getByRole('button',{name:'Remove clip from chat'}));
    rerender(<CosmosReviewFeed {...props([candidate('c',now+60)])} />);
    attach('a');
    expect(screen.getByRole('textbox',{name:'Question draft'})).toHaveValue('Keep A reachable');
  });

  it('clears attachment and draft when the source-keyed feed is replaced', () => {
    const {rerender}=render(<CosmosReviewFeed key="source-a" {...props([candidate('a')])} />);
    fireEvent.click(screen.getByRole('button',{name:'Add to chat'}));
    fireEvent.change(screen.getByRole('textbox',{name:'Question draft'}),{target:{value:'Only source A'}});
    rerender(<CosmosReviewFeed key="source-b" {...props([candidate('b')])} />);
    expect(screen.queryByTestId('chat-attachment')).not.toBeInTheDocument();
    expect(screen.queryByTestId('question-event')).not.toBeInTheDocument();
    expect(screen.getByRole('button',{name:'Send question'})).toBeDisabled();
  });

  it('shows pending clips and disables Q&A until the exact clip is ready', () => {
    render(<CosmosReviewFeed {...props([candidate('pending',now,'pending')])} />);
    expect(screen.getByText('Preparing event clip…')).toBeInTheDocument();
    expect(screen.queryByLabelText('Saved evidence clip pending')).not.toBeInTheDocument();
    expect(screen.getByRole('button',{name:'Add to chat'})).toBeDisabled();
  });

  it('clearly explains an empty candidate feed and unavailable history separately', () => {
    const {rerender}=render(<CosmosReviewFeed {...props([])} />);
    expect(screen.getByRole('heading',{name:'Waiting for a D1 candidate'})).toBeInTheDocument();
    expect(screen.queryByRole('button',{name:'Add to chat'})).not.toBeInTheDocument();
    rerender(<CosmosReviewFeed {...props([])} available={false} />);
    expect(screen.getByRole('heading',{name:'Waiting for review history'})).toBeInTheDocument();
  });
});
