Feature: VST Sensor Remove and Re-add Test
  Validate that all sensor_rtsp sensors can be removed and re-discovered via
  scan, and that recording resumes with current timelines.

  Scenario: Remove all non-file sensors and re-add via scan
    Given the VST sensor management API is accessible
    And at least one non-file sensor exists with streams
    When I remove all non-file sensors
    And I verify all sensors are removed from the sensor list
    And I wait 2 seconds and trigger a sensor scan
    And I wait 5 seconds and check recording timelines for all sensors
    Then all sensors should have current recording timelines
    And all sensor recording statuses should be active
