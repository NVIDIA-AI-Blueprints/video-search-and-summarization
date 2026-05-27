Feature: VST Stream Recorder Service API Unit Tests
  Validate that the Stream Recorder Service REST APIs respond correctly.

  Scenario: Get list of available record streams
    Given the VST stream recorder API is accessible
    When I request the list of record streams
    Then the recorder response status is 200
    And the recorder response is a valid JSON array

  Scenario: Get stream recorder service version
    Given the VST stream recorder API is accessible
    When I request the stream recorder service version
    Then the recorder response status is 200
    And the recorder response is a valid version string

  Scenario: Get stream recorder service help
    Given the VST stream recorder API is accessible
    When I request the stream recorder service help
    Then the recorder response status is 200
    And the recorder response is a list of supported API paths

  Scenario: Get stream recorder service configuration
    Given the VST stream recorder API is accessible
    When I request the stream recorder service configuration
    Then the recorder response status is 200
    And the recorder response contains configuration fields

  Scenario: Get recording timelines for all streams
    Given the VST stream recorder API is accessible
    When I request the recording timelines for all record streams
    Then the recorder response status is 200
