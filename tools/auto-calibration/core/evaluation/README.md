# ecaluation/evaluate_amc_calib.py

**This feature will be used as part of the 'Visualization Tool' for GA release. Find details in the following links.**

- [proposal - GA visualization tool](https://docs.google.com/presentation/d/1D_X8D_Ms2Zn7s27hZt2C4yCvaYgG94c9H7hJzsTwVyA/edit?slide=id.g3b1a6f596ba_0_294#slide=id.g3b1a6f596ba_0_294)
- [proposal - vss-auto-calibration result verification tool](https://docs.google.com/presentation/d/1qTAzICo9bKnzzL8kQR2Cwh1jL_Vbz92YmOYFD6AzgK4/edit?slide=id.g3bd9e451a96_0_48#slide=id.g3bd9e451a96_0_48)

**_Note: Implemented feature in this branch is for MV only as the evaluation algorithm uses triangulation in points reconstruction. Single view evaluation algorithm should be migrated from evaluation/evaluate_single_cam.py._**

The tool verifies the output `calibration.json` against GT camera and GT trajectory (or virtual GT grid points if user wants). This includes

- L2 distance
- overlay image

```
# Basic usage:
#   Required: --amc_calib, --gt_calib, --output_dir

#   GT-trajectory mode (default; add --layout_image_path to enable overlays):
     python3 core/evaluation/evaluate_amc_calib.py --amc_calib <amc.json> --gt_calib <gt.json> \
         --gt_traj <ground_truth.json> --output_dir <out> [--layout_image_path <layout.png>]

#   Grid mode (requires layout image):
     python3 core/evaluation/evaluate_amc_calib.py --amc_calib <amc.json> --gt_calib <gt.json> \
         --output_dir <out> --layout_image_path <layout.png> --use_grid --grid_size 0.5

#   Optional ROI clipping for grid:
#     add --use_roi [--roi_id <id>]

#   Optional:
#     --show_3d (interactive 3D window)
```