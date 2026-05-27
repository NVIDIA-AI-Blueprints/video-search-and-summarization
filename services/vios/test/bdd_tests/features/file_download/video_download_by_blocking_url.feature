Feature: VST Video Download via Blocking URL Validation
  Validate that videos downloaded via blocking URL API are valid media files
  and that temporary files expire after the specified expiry time

  Scenario: Download and validate videos with parallel blocking URL requests
    Given the VST API is configured for blocking URL test
    When the list of available streams for blocking URL test is fetched
    And the recording timelines for blocking URL test are fetched
    And valid time ranges for blocking URL test are selected
    Then video URLs for each stream and time range are requested with blocking mode in parallel
    And all videos are downloaded from the URLs and validated
    And video files are verified to expire after expiry time

