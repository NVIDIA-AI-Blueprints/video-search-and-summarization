Feature: PUT B-Frame Video Upload Test
  Verify that the PUT upload API correctly handles video files with B-frames

  Scenario: Upload B-frame video with PUT should succeed
    Given the VST API is configured for file uploads
    When a B-frame video is uploaded using PUT
    Then the upload should succeed with 200 OK
    And the response should contain streamId and sensorId
    And the sensor should appear in the sensor list API
    And the file should be accessible on the server

  Scenario: Upload same B-frame video twice with PUT should fail
    Given the VST API is configured for file uploads
    When a B-frame video is uploaded using PUT
    And the same B-frame video is uploaded again using PUT
    Then the second upload should fail with 409 Conflict
    And only one file should exist on the server

