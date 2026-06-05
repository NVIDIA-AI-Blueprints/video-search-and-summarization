Feature: VST File Upload Lifecycle
  Validate the lifecycle of uploaded files: re-uploading after delete,
  partial-upload cleanup on client abort, and DELETE handling for
  pass-through uploads. (Ghost-file cleanup on validation failure is
  covered separately by features/unit_tests/storage_management/
  ghost_file_cleanup.feature.)

  Scenario: Re-uploading a previously deleted file succeeds
    Given the VST API is configured for upload lifecycle tests
    And a video file has been uploaded and then deleted
    When the same file is uploaded again
    Then the re-upload succeeds
    And the re-uploaded file appears in the media file list

  Scenario: Cancelled PUT mid-upload removes partial bytes
    Given the VST API is configured for upload lifecycle tests
    When a PUT upload is started for a large file and aborted mid-transfer
    Then within 30 seconds no entry for the aborted file is in the media file list
    And no usedBytes are reported for the aborted streamId

  Scenario: External pass-through uploaded file can be deleted via DELETE
    Given the VST API is configured for upload lifecycle tests
    And a video file has been uploaded as a pass-through upload
    When the file is deleted via the storage DELETE API
    Then the DELETE response is success
    And the deleted file no longer appears in the media file list
