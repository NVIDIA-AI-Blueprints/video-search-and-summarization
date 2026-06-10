Feature: POST B-Frame Video Upload Test
  Verify that the POST multipart upload API correctly handles video files with B-frames

  Scenario: Upload B-frame video with multipart POST should succeed
    Given the VST API is configured for file uploads
    When a B-frame video is uploaded using multipart POST
    Then the upload should succeed with 200 OK
    And the response should contain file id and sensorId
    And the sensor should appear in the sensor list API
    And the file should be accessible on the server

  Scenario: Upload B-frame video multiple times should create unique files
    Given the VST API is configured for file uploads
    When the same B-frame video is uploaded multiple times using multipart POST
    Then all uploads should succeed with 200 OK
    And all files should have unique names
    And all files should exist on the server

