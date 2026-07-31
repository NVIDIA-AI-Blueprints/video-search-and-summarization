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

  # Regression: the reported endTime is the exclusive end of the last frame, so a
  # picture requested inside that final frame interval used to time out
  Scenario: Validate replay pictures at the end of a file sensor timeline
    Given the VST API is configured
    When file sensors with recorded timelines are selected
    And pictures are requested at the end of each file sensor timeline
    Then all fetched pictures are valid JPEG images

  # Regression: a picture requested at the head of a file needs that file's first
  # keyframe, which the clip reader used to drop along with the preroll buffers
  Scenario: Validate replay pictures at the start of a file sensor timeline
    Given the VST API is configured
    When file sensors with recorded timelines are selected
    And pictures are requested at the start of each file sensor timeline
    Then all fetched pictures are valid JPEG images

