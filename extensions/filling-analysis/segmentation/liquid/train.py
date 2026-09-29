# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Local RF-DETR-Seg Nano fine-tuning on explicitly weak liquid labels."""
import json, os, time
from pathlib import Path
os.environ.setdefault('CUDA_VISIBLE_DEVICES','0')
os.environ.setdefault('HF_HOME',str(Path(os.environ['FILLING_TRAINING_DIR'])/'liquid-cache/huggingface'))
os.environ['WANDB_DISABLED']='true'
from rfdetr import RFDETRSegNano
ROOT=Path(os.environ['FILLING_TRAINING_DIR']).expanduser().resolve()
if __name__=='__main__':
 start=time.monotonic()
 model=RFDETRSegNano(pretrain_weights=os.environ['FILLING_PRETRAIN_CHECKPOINT'],
   device='cuda',resolution=312,freeze_encoder=True)
 model.train(dataset_dir=str(ROOT/'liquid-dataset'),output_dir=str(ROOT/'liquid-training'),
   epochs=35,batch_size=8,grad_accum_steps=1,lr=3e-4,lr_encoder=0,lr_drop=25,
   multi_scale=False,expanded_scales=False,scale_jitter=False,do_random_resize_via_padding=False,
   use_ema=False,num_workers=2,amp_dtype='bf16',seed=20260922,
   tensorboard=False,wandb=False,mlflow=False,clearml=False,
   eval_interval=5,checkpoint_interval=10,eval_max_dets=10,
   compute_train_metrics=False,compute_val_loss=True,early_stopping=False,
   run_test=False,class_names=['visible_liquid'],progress_bar='tqdm',
   notes={'annotation_provenance':'Weak HSV surface cues and reviewed silhouette; not independent ground truth',
          'scope':'Source-adapted visible juice segmentation, full-bottle crop',
          'split':'Whole bottle cycles; synthetic repeated compositions remain a limitation',
          'inference':'RF-DETR neural mask only; no HSV mask fallback'})
 (ROOT/'liquid-artifacts/training-elapsed.json').write_text(json.dumps({'elapsed_seconds':time.monotonic()-start}))
