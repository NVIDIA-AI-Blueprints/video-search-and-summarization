# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from streaming.service import create_app
from streaming.tests.test_live import manager, STREAM


def saved_event(manager, session, number=305):
    sid=session['session_id'];event_id=f'{sid}:epoch-1:cycle-{number}:inspection'
    return {'event_id':event_id,'id':event_id,'session_id':sid,'stream_id':STREAM,'epoch':1,
            'track_id':f'{sid}:epoch-1:cycle-{number}','kind':'cycle.finalized','status':'normal',
            'measurement':session['measurement'],'clock':session['clock']}


def test_resume_preserves_counts_events_and_allocates_fresh_epoch(manager):
    session=manager.start(STREAM,'resume-proof')
    manager._notify('connected',{'connection_epoch':1})
    event=saved_event(manager,session)
    manager.store.append(event)
    manager.session['summary'].update(total=1,normal=1)
    manager.stop(session['session_id'])
    result=manager.resume(session['session_id'])
    assert result['session_id']==session['session_id'] and result['summary']['normal']==1
    assert result['cycle_number']==305 and result['current']=={}
    assert manager.latest is None
    manager._notify('connected',{'connection_epoch':2})
    state=manager.status(session['session_id'])
    assert [e['epoch'] for e in state['measurement_history']]==[1,2]
    assert manager.store.event(session['session_id'],event['event_id'])['measurement']==event['measurement']
    assert manager.resume(session['session_id'])['session_id']==session['session_id']
    with pytest.raises(HTTPException) as exc:manager.resume(str(uuid4()))
    assert exc.value.status_code==409


def test_evidence_is_bound_to_persisted_event_and_never_accepts_paths(manager,tmp_path,monkeypatch):
    monkeypatch.setenv('LIVE_DIAGNOSTICS_DIR',str(tmp_path/'diagnostics'))
    session=manager.start(STREAM,'evidence-proof');sid=session['session_id']
    event=saved_event(manager,session);event['diagnostic']={'available':True}
    manager.store.append(event)
    directory=tmp_path/'diagnostics'/sid/'epoch-1'/'cycle-305';directory.mkdir(parents=True)
    (directory/'provenance.json').write_text(json.dumps({'connection_epoch':1,'frames':[{'frame_seq':42,'pts_seconds':10.5}]}))
    _,jpeg=cv2.imencode('.jpg',np.zeros((16,16,3),np.uint8));(directory/'frame-42.jpg').write_bytes(jpeg.tobytes())
    with TestClient(create_app(manager)) as client:
        params={'session_id':sid,'event_id':event['event_id'],'frame_id':42}
        response=client.get('/live/evidence',params=params)
        assert response.status_code==200 and response.headers['content-type']=='image/jpeg'
        assert response.headers['x-live-session-id']==sid and response.headers['x-live-frame-id']=='42'
        assert response.content==jpeg.tobytes()
        for wrong in ({'session_id':str(uuid4())},{'event_id':'../../secret'},{'frame_id':43}):
            assert client.get('/live/evidence',params={**params,**wrong}).status_code==404
        items=client.get('/live/events',params={'session_id':sid}).json()['events'][0]['snapshot_evidences']
        assert len(items)==1 and items[0]['verified'] and items[0]['verification']=='exact-decoded-frame'
        assert '_path' not in items[0] and 'public_url' not in items[0]
        (directory/'frame-42.jpg').unlink();(directory/'frame-42.jpg').symlink_to(tmp_path/'other.jpg')
        (tmp_path/'other.jpg').write_bytes(jpeg.tobytes())
        assert client.get('/live/evidence',params=params).status_code==404
