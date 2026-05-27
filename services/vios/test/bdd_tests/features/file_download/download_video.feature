Feature: VST Video Download Validation
  Validate that videos downloaded from VST storage API are valid MP4 files
  when requested in parallel for multiple streams and time ranges

  Scenario: Download and validate videos with parallel requests
    Given the VST API is configured
    And test videos are uploaded to create streams
    When the list of available streams is fetched
    And the recording timelines with timeline data are fetched
    And valid time ranges from the timelines are selected
    Then videos for each stream and time range are downloaded in parallel
    And all downloaded videos are valid media files
