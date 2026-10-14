# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise deployed-source functions without importing GStreamer/CUDA services."""
import ast
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import unittest
import numpy as np
import torch
import torch.nn.functional as F

SOURCE = Path(__file__).resolve().parents[2]/'src/vlm_pipeline/video_file_frame_getter.py'
source=SOURCE.read_text()
tree=ast.parse(source)
ns={'np':np,'torch':torch,'F':F,'deque':deque,
    'logger':SimpleNamespace(debug=lambda *a:None,warning=lambda *a:None),
    'ChunkInfo':object}
for node in tree.body:
    if isinstance(node,ast.FunctionDef) and node.name in ('select_file_indices','resize_file_frames') or isinstance(node,ast.ClassDef) and node.name=='DefaultFrameSelector':
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),ns)
getter=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='VideoFileFrameGetter')
preprocess=next(n for n in getter.body if isinstance(n,ast.FunctionDef) and n.name=='_preprocess')
exec(compile(ast.Module(body=[preprocess],type_ignores=[]),str(SOURCE),'exec'),ns)

class PreprocessingTests(unittest.TestCase):
    def test_original_indices(self):
        for n in (1,2,3,4,7,8,9,30,150,151):
            frames=list(range(n));timestamps=[i*33366667 for i in frames]
            f,t,indices=ns['select_file_indices'](frames,timestamps,8)
            expected=np.linspace(0,n-1,8).round().astype(int).tolist()
            self.assertEqual(indices,expected); self.assertEqual(f,expected)
            self.assertEqual(t,[timestamps[i] for i in expected])
    def test_invalid_indices(self):
        for frames,pts,n in [([],[],8),([1],[],8),([1],[0],0)]:
            with self.assertRaises(ValueError):ns['select_file_indices'](frames,pts,n)
    def test_all_frame_boundaries(self):
        selector=ns['DefaultFrameSelector'](-1)
        selector.set_chunk(SimpleNamespace(start_pts=100,end_pts=200,pts_offset_ns=0))
        self.assertTrue(selector.selects_all_frames)
        self.assertEqual([p for p in [99,100,150,200,201] if selector.choose_frame(None,p)],[100,150,200])
        selector.set_chunk(SimpleNamespace(start_pts=300,end_pts=400,pts_offset_ns=100))
        self.assertEqual([p for p in [199,200,250,300,301] if selector.choose_frame(None,p)],[200,250,300])
    def test_resize_original_torchvision(self):
        from torchvision.transforms import Resize,InterpolationMode
        for device in ['cpu']+(['cuda'] if torch.cuda.is_available() else []):
            for shapes in [[(51,99)]*8,[(99,51)]*8,[(16,16)]*8,[(51,99),(99,51)]]:
                frames=[(torch.arange(h*w*3,device=device).reshape(h,w,3)%256).to(torch.uint8) for h,w in shapes]
                result=ns['resize_file_frames'](frames,(16,16))
                resize=Resize((16,16),interpolation=InterpolationMode.BILINEAR,antialias=True)
                for f,r in zip(frames,result):
                    self.assertTrue(torch.equal(r,resize(f.permute(2,0,1)).permute(1,2,0)))
                    self.assertEqual(r.dtype,torch.uint8);self.assertEqual(r.device,f.device)
                if shapes[0]==(16,16):self.assertIs(result,frames)
    def test_antialias_changes_pixels(self):
        from torchvision.transforms import Resize,InterpolationMode
        x=((torch.arange(65)[:,None]+torch.arange(65)[None,:])%2*255).to(torch.uint8).unsqueeze(-1).expand(65,65,3)
        aa=ns['resize_file_frames']([x],(16,16))[0]
        noaa=Resize((16,16),interpolation=InterpolationMode.BILINEAR,antialias=False)(x.permute(2,0,1)).permute(1,2,0)
        self.assertFalse(torch.equal(aa,noaa))
    def test_getter_output_and_live_bypass(self):
        frames=[torch.zeros((16,32,3),dtype=torch.uint8),torch.ones((16,32,3),dtype=torch.uint8)]
        obj=SimpleNamespace(_file_resize_active=True,_is_live=False,_file_resize_size=(8,8),_enable_jpeg_output=False,_data_type_int8=True,_do_preprocess=False)
        self.assertEqual(ns['_preprocess'](obj,frames).shape,(2,8,8,3))
        obj._is_live=True
        self.assertTrue(torch.equal(ns['_preprocess'](obj,frames),torch.stack(frames)))
        obj._is_live=False;obj._file_resize_active=False
        self.assertTrue(torch.equal(ns['_preprocess'](obj,frames),torch.stack(frames)))
    def test_mixed_resolution_before_generic_resize(self):
        obj=SimpleNamespace(_file_resize_active=True,_is_live=False,_file_resize_size=(8,8),_enable_jpeg_output=False,_data_type_int8=True,_do_preprocess=False)
        frames=[torch.zeros((16,32,3),dtype=torch.uint8),torch.ones((32,16,3),dtype=torch.uint8)]
        self.assertEqual(ns['_preprocess'](obj,frames).shape,(2,8,8,3))
    def test_parser_keeps_all_frames(self):
        method=next(n for n in getter.body if isinstance(n,ast.FunctionDef) and n.name=='_on_parser_src_buffer')
        local=dict(ns,Gst=SimpleNamespace(CLOCK_TIME_NONE=-1,PadProbeReturn=SimpleNamespace(OK='OK')))
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(SOURCE),'exec'),local)
        obj=SimpleNamespace(_is_live=False,_frame_selector=SimpleNamespace(selects_all_frames=True))
        info=SimpleNamespace(get_buffer=lambda:SimpleNamespace(pts=100))
        self.assertEqual(local['_on_parser_src_buffer'](obj,None,info),'OK')

if __name__=='__main__':
    torch.set_num_threads(2)
    unittest.main()
