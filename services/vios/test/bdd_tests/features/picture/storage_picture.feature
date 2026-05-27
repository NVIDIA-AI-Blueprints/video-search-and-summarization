Feature: VST Storage Picture Validation
  Validate that pictures retrieved from the VST storage picture API are valid JPEG images
  when requested in parallel for multiple streams and timestamps.
  The storage picture endpoint (/api/v1/storage/stream/{streamId}/picture) provides
  the same picture functionality through the storage management module.

  Scenario: Validate storage pictures with parallel requests
    Given the VST API is configured
    When the list of available streams is fetched
    And the recording timelines with timeline data are fetched
    And valid timestamps from the timelines are selected
    Then pictures for each stream and timestamp are fetched in parallel
    And all fetched pictures are valid JPEG images

  Scenario: Validate storage pictures for H265 streams
    Given the VST API is configured
    When the list of available streams is fetched
    And only H265 codec streams are selected
    And the recording timelines with timeline data are fetched
    And valid timestamps from the timelines are selected
    Then pictures for each stream and timestamp are fetched in parallel
    And all fetched pictures are valid JPEG images

  Scenario: Validate storage picture URL response
    Given the VST API is configured
    When the list of available streams is fetched
    And the recording timelines with timeline data are fetched
    And valid timestamps from the timelines are selected
    Then picture URLs for each stream and timestamp are fetched in parallel
    And all picture URL responses are valid
    And pictures downloaded from the URLs are valid JPEG images
