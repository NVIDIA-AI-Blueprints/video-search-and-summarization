Feature: Webhook notifications for file sensor lifecycle
  A file sensor created through the storage upload API must publish its camera
  lifecycle events to the configured HTTP webhook receivers.

  Scenario: File sensor lifecycle delivers add, streaming, and remove webhooks
    Given the webhook receiver is running
    And the static webhook test video is available
    When I upload a uniquely named file sensor for webhook testing
    Then the camera_add webhook is received and valid
    And the camera_streaming webhook is received and valid
    When I delete the uploaded webhook test sensor
    Then the camera_remove webhook is received and valid
