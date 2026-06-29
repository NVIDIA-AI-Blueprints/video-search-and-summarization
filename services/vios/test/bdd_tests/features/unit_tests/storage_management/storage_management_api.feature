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

  # Regression for bug 6303142 (extended to the Storage MS): the version
  # endpoint returned a hardcoded placeholder ("0.0.1") instead of the deployed
  # release/build version. The Sensor MS reports the correct build version in
  # the same deployment, so it is used as the source of truth.
  Scenario: Storage version matches the deployed build version
    Given the VST storage management API is accessible
    When I request the storage management service version
    And I request the sensor service version
    Then the storage reported version is not the placeholder "0.0.1"
    And the storage reported version matches the sensor reported build version

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

  # Regression for NVBug 6217188: a JSON array body to /storage/file/protect
  # must return 400, not crash streamprocessing-ms via uncaught Json::LogicError.
  Scenario: POST /storage/file/protect with a JSON array body is rejected, not crashed
    Given the VST storage management API is accessible
    When I POST a JSON array body to the storage file protect endpoint
    Then the storage protect request is rejected with status 400
    And the streamprocessing storage service is still alive
