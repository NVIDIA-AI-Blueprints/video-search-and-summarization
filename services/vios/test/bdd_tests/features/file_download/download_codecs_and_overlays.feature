Feature: VST Video Download - Codecs, Overlays, and Transcode Variants
  Validate that clip download supports H.265 streams, overlay rendering,
  software-only transcoding fallback when NVENC is unavailable, frame-accurate
  end-time clipping, and the direct-remux ('transcode=none') path which
  preserves the source's encoding parameters.

  # BDD-GAP-021
  Scenario: Download H.265 recorded clip returns a valid HEVC MP4
    Given the VST API is configured for codec download tests
    And an H.265 source video has been uploaded
    When an H.265 clip is downloaded from the recorded stream
    Then the returned clip probes as codec hevc
    And the returned clip duration matches the requested window within tolerance

  # BDD-GAP-022
  Scenario: Download with overlay=true embeds bboxes in the MP4
    Given the VST API is configured for codec download tests
    And a stream with stored bbox metadata exists
    When a clip is downloaded with overlay enabled
    Then sampled frames contain rendered bbox-colored regions

  # BDD-GAP-023
  Scenario: Download on a no-NVENC system falls back to software encoding
    Given the VST API is configured for codec download tests
    And the host has no NVENC available
    And a test video has been uploaded for transcode tests
    When a clip is downloaded that requires re-encoding
    Then the download completes within the configured timeout
    And the returned clip is a valid MP4

  # BDD-GAP-024
  Scenario: Downloaded clip contains the last requested frame
    Given the VST API is configured for codec download tests
    And a test video with a known long recording exists for boundary check
    When a clip ending at time T is downloaded
    Then the last decoded frame PTS is at least T minus one frame interval

  # BDD-GAP-025
  Scenario: Direct-remux download preserves input bitrate/fps/keyframe-interval
    Given the VST API is configured for codec download tests
    And a known-bitrate test video has been uploaded
    When a clip is downloaded with transcode preset 'none'
    Then the output bitrate is within tolerance of the input
    And the output fps matches the input
    And the output keyframe interval matches the input within tolerance
