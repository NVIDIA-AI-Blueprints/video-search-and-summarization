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

  # Regression for NVBug 6221886: file/list must honour ?tag= (exact) and
  # ?eventInfo= (substring) filters against the persisted metadata.
  Scenario: Filter the media file list by an exact tag value
    Given the VST storage management API is accessible
    And three media files are uploaded with distinct tag and eventInfo metadata
    And the uploaded files appear in the file list with their tag metadata
    When I request the file list filtered by the tag shared by two of the files
    Then only the files carrying that tag are returned

  Scenario: Filter the media file list by an eventInfo substring
    Given the VST storage management API is accessible
    And three media files are uploaded with distinct tag and eventInfo metadata
    And the uploaded files appear in the file list with their tag metadata
    When I request the file list filtered by an eventInfo substring shared by two of the files
    Then only the files whose eventInfo contains that substring are returned
