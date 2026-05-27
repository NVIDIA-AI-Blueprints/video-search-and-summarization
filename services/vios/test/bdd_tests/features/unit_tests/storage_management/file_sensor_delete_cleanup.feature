Feature: VST Sensor Delete Removes File-Sensor Recordings From Disk
  DELETE /api/v1/sensor/{id} on a file-type sensor (created by PUT
  /api/v1/storage/file) must remove the physical recording(s) backing the
  sensor's streams along with the sensor record. Prior to this fix the file
  was retained on disk and only the sensor row was wiped, leaving a
  permanently orphaned upload that no API could clean up.

  Background:
    Given the VST storage management API is accessible
    And the static test video is available

  Scenario: Deleting a file sensor removes its recording from disk
    When I upload a file sensor successfully
    And I delete that file sensor via the sensor delete API
    Then the deleted sensor's file should no longer be on the storage service
    And the deleted sensor should no longer be in the sensor list

  Scenario: Deleting one file sensor leaves another file sensor's recording intact
    When I upload two file sensors successfully
    And I delete the first uploaded file sensor via the sensor delete API
    Then the first uploaded sensor's file should no longer be on the storage service
    And the second uploaded sensor's file should still be on the storage service
    And the second uploaded sensor should still be in the sensor list

  Scenario: Deleting a multi-stream file sensor removes all of its recordings
    When I upload a file sensor successfully
    And I add a second upload to the same sensor via the merge path
    And I delete that file sensor via the sensor delete API
    Then both uploaded files should no longer be on the storage service
    And the deleted sensor should no longer be in the sensor list

  Scenario: After sensor delete the file is no longer downloadable
    When I upload a file sensor successfully
    And I delete that file sensor via the sensor delete API
    Then a download of the deleted file should return 4xx
