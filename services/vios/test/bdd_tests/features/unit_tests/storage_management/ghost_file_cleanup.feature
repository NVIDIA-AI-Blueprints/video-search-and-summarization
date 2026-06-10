Feature: VST Storage PUT API Ghost-File Cleanup (bug 5757067)
  Regression coverage for bug 5757067: when the PUT /api/v1/storage/file
  raw upload fails, the partial file written to disk and any sensor entry
  the upload may have created MUST be cleaned up so the API caller does
  not see a ghost file or a dangling sensor record.

  Two earlier patches addressed the pre-addFile() failure paths
  (commit 1d8cd08a). These scenarios exercise the remaining
  post-addFile() failure paths (invalid timestamp, parse error,
  duplicate-record) plus regression coverage for the pre-addFile()
  paths so they don't regress.

  Background:
    Given the VST storage management API is accessible
    And the static test video is available

  # ---------------------------------------------------------------------
  # Pre-addFile() failure paths (regression coverage for commit 1d8cd08a)
  # ---------------------------------------------------------------------

  Scenario: Garbage body fails media validation and leaves no file
    When I PUT a small non-video body to /storage/file
    Then the upload should be rejected with 4xx
    And no file should remain on the storage service
    And no sensor should be left behind

  Scenario: Body smaller than declared Content-Length leaves no file
    When I PUT a body that is shorter than its Content-Length to /storage/file
    Then the upload should be rejected with 4xx
    And no file should remain on the storage service
    And no sensor should be left behind

  Scenario: Concurrent upload of the same filename leaves no extra file
    When I PUT the static video twice in parallel with the same filename
    Then exactly one upload should succeed
    And the conflicting upload should be rejected with 4xx
    And only the successful upload should be on disk
    And only the successful sensor should be in the deployment

  # ---------------------------------------------------------------------
  # Post-addFile() failure paths (the new patch)
  # ---------------------------------------------------------------------

  Scenario: Pre-epoch timestamp (negative epoch) leaves no file or sensor
    When I PUT the static video with a pre-epoch timestamp to /storage/file
    Then the upload should be rejected with 4xx
    And the response should mention a timestamp problem
    And no file should remain on the storage service
    And no sensor should be left behind

  Scenario: Re-using the same filename is rejected and leaves no second file
    When I successfully PUT the static video once
    And I PUT the static video again to the same filename
    Then the second upload should be rejected with 4xx
    And only the first upload's file should be on disk
    And only the first upload's sensor should be in the deployment

  Scenario: Failed merge upload preserves the original sensor and its first stream
    When I successfully PUT the static video once
    And I PUT a different filename to the same sensorId with a pre-epoch timestamp
    Then the second upload should be rejected with 4xx
    And the original sensor and its first stream should still be present
    And the second upload's file should not be on disk

  # ---------------------------------------------------------------------
  # Common pre-write rejections (no file should ever exist)
  # ---------------------------------------------------------------------

  Scenario Outline: Pre-write rejection writes no file and creates no sensor
    When I PUT a request that <reject_reason>
    Then the upload should be rejected with 4xx
    And no file should remain on the storage service
    And no sensor should be left behind

    Examples:
      | reject_reason                       |
      | omits the Content-Length header     |
      | declares Content-Length 0           |
      | uses a filename containing spaces   |

  # ---------------------------------------------------------------------
  # Happy path - file IS retained on success
  # ---------------------------------------------------------------------

  Scenario: Successful upload retains the file and sensor
    When I PUT the static video with a valid sensorId and timestamp
    Then the upload should succeed with 2xx
    And the uploaded file should remain on the storage service
    And the uploaded sensor should appear in the sensor list
