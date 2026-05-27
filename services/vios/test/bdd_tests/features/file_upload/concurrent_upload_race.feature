Feature: Concurrent File Upload Race Condition Test
  Verify that the new PUT upload API correctly handles concurrent uploads
  of the same filename without race conditions

  Scenario: Upload same file concurrently should result in only one success
    Given the VST API is configured for file uploads
    And a test video file is prepared
    When multiple threads upload the same file concurrently using new PUT API
    Then only one upload should succeed with 200 OK
    And all other uploads should fail with 409 Conflict
    And only one file should exist on the server

  Scenario: Upload different files concurrently should all succeed
    Given the VST API is configured for file uploads
    And multiple test video files are prepared with unique names
    When multiple threads upload different files concurrently using new PUT API
    Then all uploads should succeed with 200 OK
    And all files should exist on the server
