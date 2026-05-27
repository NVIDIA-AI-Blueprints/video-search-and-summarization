Feature: Upload SensorId Parameter Handling
  Verify that the upload API correctly handles sensorId parameters

  Scenario: Upload without sensorId should generate random UUID
    Given the VST API is configured for file uploads
    When a file is uploaded without sensorId parameter
    Then the upload should succeed with 200 OK
    And the response should contain a generated sensorId
    And the sensorId should be a valid UUID
    And the sensor should appear in the sensor list API
