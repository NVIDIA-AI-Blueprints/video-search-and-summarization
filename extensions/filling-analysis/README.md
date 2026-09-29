# Filling analysis inside VSS

Adds a native VSS tab, Filling API, recorded GPU segmentation, live RTSP inspection, CLI tools and an operational skill. Search, Alerts, Video Management and OpenClaw remain the host application.

Use [the QA build/deployment recipe](deployment/qa/README.md). Weights and recordings are external. Source checkout does not recreate an initialized deployment.

Original multi-shot footage retains explicit legacy image processing. Fixed-camera inspection uses RF-DETR masks for visible height; overflow is separately calibrated exterior-color evidence. Missing masks remain unreadable. Weak labels and camera adaptation limit generalization; height is not volume.

See [live operation](LIVE-OPERATIONS.md) and [segmentation](segmentation/README.md).
