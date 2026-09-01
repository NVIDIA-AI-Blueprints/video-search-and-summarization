Feature: VST Live MPEG-DASH Stream Validation
  Validate that a live H.264 stream can be packaged once and delivered over HTTP.

  Scenario: Start and fetch a live DASH manifest
    Given the live DASH API is configured
    When a DASH viewer is started for an available live stream
    Then the DASH manifest becomes available
    And the DASH viewer lease can be released
