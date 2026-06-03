Feature: Upload Timestamp Parameter Handling
  Verify that the upload API correctly handles timestamp parameters

  Scenario: Upload without timestamp should use epoch time
    Given the VST API is configured for file uploads
    When a file is uploaded without timestamp parameter
    Then the upload should succeed with 200 OK
    And the sensor should appear in the sensor list API
    And the timeline should show epoch time as start time

  Scenario: Upload with timestamp should use provided timestamp
    Given the VST API is configured for file uploads
    When a file is uploaded with specific timestamp
    Then the upload should succeed with 200 OK
    And the sensor should appear in the sensor list API
    And the timeline should show the provided timestamp as start time
