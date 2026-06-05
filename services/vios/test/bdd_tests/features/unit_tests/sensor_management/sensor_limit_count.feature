Feature: VST Sensor Limit Count After File Sensor Delete (bug 6167064)
  Regression coverage for bug 6167064: in scaled VST deployments the
  streamprocessing service kept stale file-sensor entries in its in-memory
  cache after sensor-ms processed a DELETE /api/v1/sensor/{id}. Because the
  cache feeds isSpaceForNewSensor(), subsequent uploads were rejected with
  "Sensors count limit reached" even though the shared DB had free slots.

  The fix sources the count from the DB in scaled builds. These scenarios
  fill the deployment up to max_devices_supported with file uploads, exercise
  the sensor-delete path, and verify the count is accurately decremented so
  the next upload succeeds.

  Background:
    Given the VST sensor management API is accessible
    And the configured sensor limit is known
    And the deployment is filled to the sensor limit with uploaded file sensors

  Scenario: Deleting a file sensor via sensor API frees a slot for a new upload
    When I delete one uploaded file sensor via the sensor delete API
    And I upload another file sensor
    Then the new upload should succeed
    And the sensor count should match the sensor limit

  Scenario: Upload at the sensor limit is rejected
    When I upload another file sensor
    Then the upload should be rejected with sensor limit reached

  Scenario: Deleting multiple file sensors frees the same number of slots
    When I delete 3 uploaded file sensors via the sensor delete API
    Then the sensor count should drop by 3
    And I can upload 3 more file sensors
    And the sensor count should match the sensor limit

  Scenario: Sensor count check survives a cache-refreshing API call between delete and upload
    When I delete one uploaded file sensor via the sensor delete API
    And I list all sensors to refresh the cache
    And I upload another file sensor
    Then the new upload should succeed

  Scenario: Deleting a file sensor removes the recording from disk
    When I delete one uploaded file sensor via the sensor delete API
    Then the uploaded media file no longer exists on the storage service

  Scenario: Deleting an RTSP sensor frees a slot for a file upload
    When I delete one RTSP sensor via the sensor delete API
    And I upload another file sensor
    Then the new upload should succeed
    And the sensor count should match the sensor limit

  Scenario: Deleting a file sensor frees a slot for a new RTSP add
    When I delete one uploaded file sensor via the sensor delete API
    And I add an RTSP sensor
    Then the new RTSP add should succeed
    And the sensor count should match the sensor limit

  Scenario: Adding an RTSP sensor at the limit is rejected
    When I add an RTSP sensor
    Then the sensor add should be rejected with sensor limit reached

  Scenario: Mixed deletes and adds across both sensor types maintain an accurate count
    When I delete 2 RTSP sensors and 1 uploaded file sensor via the sensor delete API
    Then the sensor count should drop by 3
    And I can upload 2 more file sensors
    And I add 1 RTSP sensor
    Then the sensor count should match the sensor limit
