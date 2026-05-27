Feature: VST Replay Stream Service API Unit Tests
  Validate that the Replay Stream Service REST APIs respond correctly.
  WebRTC signaling endpoints are excluded as they require a full WebRTC handshake.

  Scenario: Get list of available replay streams
    Given the VST replay stream API is accessible
    When I request the list of replay streams
    Then the replay response status is 200
    And the replay response is a valid JSON array

  Scenario: Get replay stream service version
    Given the VST replay stream API is accessible
    When I request the replay stream service version
    Then the replay response status is 200
    And the replay response is a valid version string

  Scenario: Get replay stream service help
    Given the VST replay stream API is accessible
    When I request the replay stream service help
    Then the replay response status is 200
    And the replay response is a list of supported API paths

  Scenario: Get replay stream service configuration
    Given the VST replay stream API is accessible
    When I request the replay stream service configuration
    Then the replay response status is 200
    And the replay response contains configuration fields
