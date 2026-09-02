# Calibration Handoff

Load this reference when standalone RTVI-CV-3D/MV3DT input does not already
have usable calibration for the same cameras.

## Ownership rule

Do not implement or summarize the AMC workflow here. Hand off by skill name to
`vss-generate-video-calibration` and follow that skill's current `SKILL.md` and
selected input-mode reference at runtime. It owns AMC deployment, platform and
service prerequisites, local-video/RTSP handling, detector selection,
calibration, optional refinement, and its output contract.

This reference owns only the standalone deployment state that must survive the
handoff and the MCT-specific validation required before resuming.

## Preserve before handoff

- Input mode: local files or RTSP streams.
- Explicit `<camera-id>=<path-or-url>` mappings, or the ordered inputs when IDs
  have not been assigned yet.
- Dataset/project label and expected camera count.
- Requested live OSD, saved grid output, live BEV, or saved BEV output.
- Bundled or external broker selection and any external broker endpoints.
- Existing layout/map or other user-provided assets.

## Delegate

1. Invoke `vss-generate-video-calibration` by name with the preserved input
   information. Do not hardcode its filesystem path.
2. Let that skill collect any missing calibration-specific inputs and make all
   AMC decisions. Do not repeat its commands, endpoints, prerequisites,
   detector options, VIOS wiring, polling, or error handling here.
3. If it cannot complete, preserve the standalone request and stop. Do not
   fabricate calibration, reuse sample calibration, or continue with stale
   artifacts.
4. When it completes, read and follow its current downstream-consumer contract
   to obtain the calibration and MV3DT/BEV artifacts. Do not pin an AMC API or
   output-directory layout in this skill.

## Resume standalone deployment

Before returning to `references/configure-cameras.md`:

1. Resolve the returned `calibration.json` and validate only sensors where
   `type == "camera"`.
2. Require at least two non-empty, safe, unique camera IDs and match them
   exactly to the preserved file/RTSP mappings. Ask for a mapping only when the
   IDs cannot be matched unambiguously.
3. Generate standalone `camInfo` through the existing RTVI-CV-3D configuration
   flow; do not substitute calibration-service internal files for that flow.
4. Stage the BEV assets supplied through the current calibration output
   contract. BEV visualization is ready only when the selected
   `BEV_DATASET_PATH` contains both `map.png` and `transforms.yml`; otherwise
   continue only with outputs that do not require BEV and report the missing
   asset.
5. Resume with the original input, broker, and visualization choices.
