Feature: VST Recent Video Download Validation
  Validate that recent videos (relative to current time) downloaded from VST storage API
  are valid MP4 files when requested in parallel for multiple streams.
  This tests the ability to download video clips ending at or near the current time.
  Transcode/overlay mode is controlled by the 'enable_transcode' config setting.

  Scenario: Download and validate recent videos with parallel requests
    Given the VST API is configured for recent download
    And test videos are uploaded to create streams for download
    When the list of available streams is fetched for recent download
    And recent time ranges are calculated based on offset and duration
    Then recent videos for each stream are downloaded in parallel
    And all downloaded recent videos are valid media files

