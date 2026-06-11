Feature: VST API Security Contracts
  Validate that the VST REST API enforces basic security contracts at the
  request boundary: parameterised SQL queries, no path traversal via
  absolute filenames.

  Background:
    Given the VST API is configured for security tests

  Scenario: SQL-injection payload in sensor name does not alter the database
    When I POST to sensor/add with a SQL-injection payload as the name
    Then the sensor add response status is not 500
    And the sensors table is intact and queryable
    And the sensor count reflects only the legitimate insert
    And I clean up the SQL-injection sensor if it was persisted

  Scenario: Storage PUT upload rejects an absolute-path filename
    When I PUT a file using an absolute-path filename
    Then the upload response status is 4xx
    And the upload response status is not 500
    And no file is created outside the configured storage root

  Scenario Outline: Malformed body to /storage/file/protect does not crash the service
    # Regression guard — POST with a non-object JSON body must
    # be rejected at the validation layer with a 4xx response and must not
    # propagate Json::LogicError up to the signal handler (which previously
    # SIGABRT'd the streamprocessing-ms container, taking down all live/replay
    # sessions for ~28s).
    When I POST <body_kind> body to /storage/file/protect
    Then the protect response status is 4xx
    And the protect response status is not 5xx
    And /storage/info still returns 200 immediately afterwards

    Examples:
      | body_kind   |
      | array       |
      | string      |
      | number      |
      | boolean     |
      | null        |
