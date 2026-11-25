# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse, json, time
from pathlib import Path
import torch
from PIL import Image
from d1_runtime import load_model, ROOT
from d1_protocol import model_arguments

p = argparse.ArgumentParser()
p.add_argument('--image', help='Optional existing image file mounted inside /workspace')
a = p.parse_args()
model, lock = load_model()
questions = {'refund': {'type': 'noul', 'instructions': 'Is the customer requesting a refund?'}}
state = 'I was charged twice. Please refund the duplicate charge.'
with torch.inference_mode():
    model.system_one(state, questions)
    torch.cuda.synchronize()
    start = time.perf_counter()
    answer = model.system_one(state, questions)
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - start
    assert 'refund' in answer['answers']
    assert 0 <= answer['answers']['refund']['noul'] <= 1
    result = {'model': lock, 'gpu': torch.cuda.get_device_name(), 'text_result': answer,
              'warm_text_call_ms': elapsed * 1000, 'image_test': 'not_run'}
    if a.image:
        with Image.open(a.image) as im:
            image = im.convert('RGB')
        result['image_result'] = model.system_one(None,
            {'person': {'type': 'noul', 'instructions': 'Is a person visible in the image?'}},
            **model_arguments([image], None, lock.get('input_api') == 'media'))
        result['image_test'] = 'completed_not_accuracy_scored'
(ROOT / 'results').mkdir(exist_ok=True)
(ROOT / 'results/smoke.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
