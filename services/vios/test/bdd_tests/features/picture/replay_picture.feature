Feature: VST Picture Validation
  Validate that pictures retrieved from VST replay API are valid JPEG images
  when requested in parallel for multiple streams and timestamps

  Scenario: Validate replay pictures with parallel requests
    Given the VST API is configured
    When the list of available streams is fetched
    And the recording timelines with timeline data are fetched
    And valid timestamps from the timelines are selected
    Then pictures for each stream and timestamp are fetched in parallel
    And all fetched pictures are valid JPEG images

  Scenario: Validate replay pictures for H265 streams
    Given the VST API is configured
    When the list of available streams is fetched
    And only H265 codec streams are selected
    And the recording timelines with timeline data are fetched
    And valid timestamps from the timelines are selected
    Then pictures for each stream and timestamp are fetched in parallel
    And all fetched pictures are valid JPEG images

  Scenario: Validate replay pictures for disconnected H265 sensor
    Given the VST API is configured
    When the list of available streams is fetched
    And only H265 codec streams are selected
    And the recording timelines with timeline data are fetched
    And an H265 sensor is disconnected
    And valid timestamps from the timelines are selected
    Then pictures for each stream and timestamp are fetched in parallel
    And all fetched pictures are valid JPEG images

