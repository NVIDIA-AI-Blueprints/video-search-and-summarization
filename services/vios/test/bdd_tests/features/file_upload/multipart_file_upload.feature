Feature: Multipart File Upload (POST) Test
  Verify that the POST multipart upload API correctly handles file uploads
  with metadata including sensorId support

  Scenario: Upload file with multipart POST should succeed
    Given the VST API is configured for file uploads
    When a file is uploaded using multipart POST with metadata
    Then the upload should succeed with 200 OK
    And the response should contain file id, sensorId, and streamId
    And the sensor should appear in the sensor list API
    And the file should appear in the file list API

  Scenario: Upload multiple files with same sensorId should create sub-streams
    Given the VST API is configured for file uploads
    When multiple files are uploaded with same sensorId using multipart POST
    Then all uploads should succeed with 200 OK
    And all files should have the same sensorId
    And files should have different streamIds
    And one should be main stream and others should be sub-streams
    And all files should be grouped under same sensorId in file list

  Scenario: Upload same filename twice with multipart POST should create unique files
    Given the VST API is configured for file uploads
    When the same filename is uploaded twice using multipart POST
    Then both uploads should succeed with 200 OK
    And files should have different names with suffix
    And both files should exist on server

