Feature: VST Live Stream Service API Unit Tests
  Validate that the Live Stream Service REST APIs respond correctly.
  WebRTC signaling endpoints are excluded as they require a full WebRTC handshake.

  Scenario: Get list of available live streams
    Given the VST live stream API is accessible
    When I request the list of live streams
    Then the response status is 200
    And the response is a valid JSON array

  Scenario: Get live stream service version
    Given the VST live stream API is accessible
    When I request the live stream service version
    Then the response status is 200
    And the response is a valid version string

  Scenario: Get live stream service help
    Given the VST live stream API is accessible
    When I request the live stream service help
    Then the response status is 200
    And the response is a list of supported API paths

  Scenario: Get live stream service configuration
    Given the VST live stream API is accessible
    When I request the live stream service configuration
    Then the response status is 200
    And the response contains configuration fields

  Scenario: Get live picture URL for a stream
    Given the VST live stream API is accessible
    And at least one live stream exists
    When I request a live picture URL for the first stream
    Then the response status is 200
    And the response contains a picture URL
