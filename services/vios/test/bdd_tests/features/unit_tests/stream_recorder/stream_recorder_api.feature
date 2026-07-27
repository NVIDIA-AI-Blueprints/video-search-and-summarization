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

  # Regression for bug 6303142: the Record MS version endpoint returned a
  # hardcoded placeholder ("0.0.1") instead of the deployed release/build
  # version. The Sensor MS reports the correct build version in the same
  # deployment, so we use it as the source of truth (as the bug report did)
  # rather than hardcoding a release string that changes every release.
  Scenario: Stream recorder version matches the deployed build version
    Given the VST stream recorder API is accessible
    When I request the stream recorder service version
    And I request the sensor service version
    Then the recorder reported version is not the placeholder "0.0.1"
    And the recorder reported version matches the sensor reported build version

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
