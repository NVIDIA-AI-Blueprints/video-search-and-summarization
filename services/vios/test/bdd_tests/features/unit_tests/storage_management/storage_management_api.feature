Feature: VST Storage Management Service API Unit Tests
  Validate that the Storage Management Service REST APIs respond correctly.

  Scenario: Get total storage size
    Given the VST storage management API is accessible
    When I request the total storage size
    Then the storage response status is 200

  Scenario: Get storage info
    Given the VST storage management API is accessible
    When I request the storage info
    Then the storage response status is 200
    And the storage info contains total used and available fields

  Scenario: Get storage management service version
    Given the VST storage management API is accessible
    When I request the storage management service version
    Then the storage response status is 200
    And the storage response is a valid version string

  Scenario: Get storage management service help
    Given the VST storage management API is accessible
    When I request the storage management service help
    Then the storage response status is 200
    And the storage response is a list of supported API paths

  Scenario: Get storage management service configuration
    Given the VST storage management API is accessible
    When I request the storage management service configuration
    Then the storage response status is 200
    And the storage response contains configuration fields

  Scenario: Get list of all media files
    Given the VST storage management API is accessible
    When I request the list of all media files
    Then the storage response status is 200

  Scenario: Get protected file list
    Given the VST storage management API is accessible
    When I request the protected file list
    Then the storage response status is 200

  # Regression for NVBug 6164097: DELETE must accept any identifier the upload
  # returns for a file (id or streamId), not just id.
  Scenario: A file uploaded via POST can be deleted using the returned streamId
    Given the VST storage management API is accessible
    And the static test video is available
    When I upload a media file via POST to the storage service
    Then the upload response contains both an id and a streamId
    When I delete the uploaded file using the returned streamId
    Then the storage delete response status is 200
    And the uploaded file is no longer present on the storage service

  Scenario: A file uploaded via POST can still be deleted using the returned id
    Given the VST storage management API is accessible
    And the static test video is available
    When I upload a media file via POST to the storage service
    And I delete the uploaded file using the returned id
    Then the storage delete response status is 200
    And the uploaded file is no longer present on the storage service
