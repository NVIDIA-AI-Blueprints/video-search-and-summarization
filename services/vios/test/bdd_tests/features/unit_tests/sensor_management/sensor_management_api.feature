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

  # Bug 6177255: GET /v1/sensor/configuration returned null (not []) for the
  # required, non-nullable array fields ntpServers and deviceDiscoveryInterfaces
  # when no values were configured, violating the GetConfiguration swagger contract.
  Scenario: Configuration required array fields are arrays not null
    Given the VST sensor management API is accessible
    When I request the sensor management service configuration
    Then the sensor response status is 200
    And the configuration required array fields are JSON arrays

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
