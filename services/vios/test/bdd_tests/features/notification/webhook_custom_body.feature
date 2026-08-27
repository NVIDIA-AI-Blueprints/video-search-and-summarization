Feature: Custom webhook body templates
  A webhook request entry may configure a JSON body template. A valid template
  is rendered against the notification into the complete request body, without
  the default fields such as webhook_id. An invalid template is rejected when
  the configuration is loaded, and only that request entry is skipped. Without
  a body, user_defined_metadata is still merged verbatim into event.metadata
  of the default body; with a body, user_defined_metadata is ignored.

  The template test cases live in data/webhook_bdd_config.json; apply them to
  the deployed notification config with scripts/update_notification_config.py
  and restart VST before running this feature. Valid cases cover scalar,
  object, and array placeholders, missing paths rendering as "", preserved
  literal types, an empty {} body, the 32-level depth boundary, the
  default-shaped camera_streaming body with resolved metadata placeholders,
  body precedence over user_defined_metadata, the user_defined_metadata
  passthrough, and the Elasticsearch delete-by-query body on camera_remove.
  Invalid cases cover malformed, embedded, and empty placeholders, braces in
  property names, bare reserved braces, and a 33-level body.

  Background:
    Given the webhook receiver is running
    And the static webhook test video is available
    And the custom body test cases are loaded

  Scenario: Valid custom body templates deliver their rendered bodies
    When I upload a uniquely named file sensor for webhook testing
    Then every valid camera_add and camera_streaming custom body webhook delivers its rendered body
    When I delete the uploaded webhook test sensor
    Then every valid camera_remove custom body webhook delivers its rendered body

  Scenario: Invalid custom body templates are skipped while valid siblings deliver
    When I upload a uniquely named file sensor for webhook testing
    Then the default camera_add webhook is delivered
    And no invalid custom body webhook is delivered
