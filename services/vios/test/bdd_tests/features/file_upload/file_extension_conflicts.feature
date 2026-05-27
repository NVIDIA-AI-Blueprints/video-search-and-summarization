Feature: File Extension Conflict Handling
  Verify that the upload API correctly handles filename conflicts
  with and without file extensions

  Scenario: Upload file without extension when file with extension exists should fail
    Given the VST API is configured for file uploads
    And a test video file with extension exists on server
    When a file without extension is uploaded with the same base name
    Then the upload should fail with 409 Conflict
    And the error message should indicate file already exists

  Scenario: Upload same filename with extension twice should fail
    Given the VST API is configured for file uploads
    When a file with extension is uploaded to server
    And the same filename with extension is uploaded again
    Then the second upload should fail with 409 Conflict
    And the error message should indicate file already exists

  Scenario: Upload file with extension when file without extension exists should fail
    Given the VST API is configured for file uploads
    And a test video file without extension exists on server
    When a file with extension is uploaded with the same base name
    Then the upload should fail with 409 Conflict
    And the error message should indicate file already exists
