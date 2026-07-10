Feature: Webhook notifications for file sensor lifecycle
  A file sensor created through the storage upload API must publish its camera
  lifecycle events to the configured HTTP webhook receivers.

  Background:
    Given the webhook receiver is running
    And the static webhook test video is available

  Scenario: File sensor lifecycle delivers to file filtered webhook receivers
    When I upload a uniquely named file sensor for webhook testing
    Then the camera_add webhook is received and valid
    And the camera_streaming webhook is received and valid
    When I delete the uploaded webhook test sensor
    Then the camera_remove webhook is received and valid

  Scenario: Unfiltered webhook receiver accepts a file camera event
    When I upload a uniquely named file sensor for webhook testing
    Then the unfiltered camera_add webhook is received and valid

  Scenario: RTSP filtered webhook receiver rejects a file camera event
    When I upload a uniquely named file sensor for webhook testing
    Then the camera_add webhook is received and valid
    And the rtsp-only camera_add webhook is not received
