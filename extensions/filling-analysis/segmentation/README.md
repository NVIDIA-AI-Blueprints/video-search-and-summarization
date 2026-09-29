# GPU bottle and liquid segmentation

RF-DETR-Seg 2XLarge finds bottles; custom Seg Nano predicts liquid in padded bottle crops, clipped by the bottle mask. Recorded analysis processes every frame. Live RTSP processing uses bounded queues and reports drops/age.

Both neural models run on CUDA. Exterior overflow is a separate CPU color signal. Visible height is not volume; absent masks do not certify empty bottles.

See [QA assets/build](../deployment/qa/README.md). Model weights, datasets and engines are external. Weak HSV/silhouette labels, whole-cycle splits and repeated synthetic compositions limit independent evaluation.
