Feature: VST Sensor Management Service API Unit Tests
  Validate that the Sensor Management Service REST APIs respond correctly.

  Scenario: Get list of sensors
    Given the VST sensor management API is accessible
    When I request the list of sensors
    Then the sensor response status is 200
    And the sensor response is a valid JSON array

  Scenario: Get status of all sensors
    Given the VST sensor management API is accessible
    When I request the status of all sensors
    Then the sensor response status is 200

  Scenario: Get streams of all sensors
    Given the VST sensor management API is accessible
    When I request the streams of all sensors
    Then the sensor response status is 200
    And the sensor response is a valid JSON array

  Scenario: Get sensor management service version
    Given the VST sensor management API is accessible
    When I request the sensor management service version
    Then the sensor response status is 200
    And the sensor response is a valid version string

  Scenario: Get sensor management service help
    Given the VST sensor management API is accessible
    When I request the sensor management service help
    Then the sensor response status is 200
    And the sensor response is a list of supported API paths

  Scenario: Get sensor management service configuration
    Given the VST sensor management API is accessible
    When I request the sensor management service configuration
    Then the sensor response status is 200
    And the sensor response contains configuration fields

  Scenario: Get QOS stats for all sensors
    Given the VST sensor management API is accessible
    When I request the QOS stats
    Then the sensor response status is 200

  Scenario: Get system stats
    Given the VST sensor management API is accessible
    When I request the system stats
    Then the sensor response status is 200

  Scenario: Get recording timelines for all sensors
    Given the VST sensor management API is accessible
    When I request the recording timelines for all sensors
    Then the sensor response status is 200

  Scenario: Get streams for a specific sensor
    Given the VST sensor management API is accessible
    And at least one sensor exists
    When I request streams for the first sensor
    Then the sensor response status is 200

  Scenario: Get status for a specific sensor
    Given the VST sensor management API is accessible
    And at least one sensor exists
    When I request status for the first sensor
    Then the sensor response status is 200

  Scenario: Get info for a specific sensor
    Given the VST sensor management API is accessible
    And at least one sensor exists
    When I request info for the first sensor
    Then the sensor response status is 200

  Scenario: Get timelines for a specific sensor
    Given the VST sensor management API is accessible
    And at least one sensor exists
    When I request timelines for the first sensor
    Then the sensor response status is 200

  # Bug 6216283: /sensor/{id}/status for a non-existent sensor must return a
  # 404 error envelope (camelCase per the swagger) instead of a 200 success
  # body that embeds the error and a "state":"offline" field.
  Scenario: Status of a non-existent sensor returns a 404 camelCase error envelope
    Given the VST sensor management API is accessible
    When I request status for a non-existent sensor
    Then the sensor response status is 404
    And the sensor error body uses camelCase keys with error code "CameraNotFoundError"
    And the sensor error body has no success "state" field

  # Bug 6216283: every Sensor Management endpoint must emit one consistent
  # camelCase error envelope on error, not the snake_case variant.
  Scenario Outline: Error envelope for a non-existent sensor is consistent camelCase across endpoints
    Given the VST sensor management API is accessible
    When I request "<action>" for a non-existent sensor
    Then the sensor response status is 404
    And the sensor error body uses camelCase keys with error code "CameraNotFoundError"

    Examples:
      | action    |
      | streams   |
      | timelines |
      | network   |
