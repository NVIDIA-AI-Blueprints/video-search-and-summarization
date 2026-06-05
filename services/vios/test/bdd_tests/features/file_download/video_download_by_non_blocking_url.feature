Feature: VST Video Download via Non-Blocking URL Validation
  Validate that videos downloaded via non-blocking URL API are valid media files
  and can be accessed with different streamable options

  Scenario: Download and validate videos with parallel non-blocking URL requests
    Given the VST API is configured for non-blocking URL test
    When the list of available streams for non-blocking URL test is fetched
    And the recording timelines for non-blocking URL test are fetched
    And valid time ranges for non-blocking URL test are selected
    Then video URLs for each stream and time range are requested with non-blocking mode in parallel
    And all videos are downloaded with streamable true option and validated
    And all videos are downloaded with streamable false option and validated
    And video files are verified to expire after expiry time

