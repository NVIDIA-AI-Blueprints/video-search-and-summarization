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

  Scenario: File sensor camera_streaming event carries the complete notification schema
    When I upload a uniquely named file sensor for webhook testing
    Then the camera_streaming notification has the expected structure
    And the camera_streaming notification values are valid
    And the camera_streaming notification metadata is valid for the camera type

  # The RTSP scenario is skipped unless notification_tests.test_parameters.rtsp_sensor
  # is set in config.json. It also carries the camera_streaming schema assertions:
  # every RTSP scenario adds the same configured URL, and VST rejects a duplicate by
  # URL regardless of the unique sensor name, so a second scenario adding it again is
  # rejected with "Sensor exists already".
  Scenario: RTSP sensor lifecycle delivers to rtsp filtered webhook receivers
    When I add the configured RTSP sensor for webhook testing
    Then the rtsp camera_add webhook is received and valid
    And the rtsp camera_streaming webhook is received and valid
    And the camera_streaming notification has the expected structure
    And the camera_streaming notification values are valid
    And the camera_streaming notification metadata is valid for the camera type
    When I delete the added RTSP webhook test sensor
    Then the rtsp camera_remove webhook is received and valid

  Scenario: Unfiltered webhook receiver accepts a file camera event
    When I upload a uniquely named file sensor for webhook testing
    Then the unfiltered camera_add webhook is received and valid

  Scenario: RTSP filtered webhook receiver rejects a file camera event
    When I upload a uniquely named file sensor for webhook testing
    Then the camera_add webhook is received and valid
    And the rtsp-only camera_add webhook is not received
