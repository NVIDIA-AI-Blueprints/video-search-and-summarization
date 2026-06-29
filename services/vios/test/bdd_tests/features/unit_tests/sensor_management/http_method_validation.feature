Feature: VST API HTTP method validation (405 Method Not Allowed)
  The shared HTTP request handler must enforce method validation uniformly.
  Read-only (GET) endpoints must reject POST/PUT/DELETE with 405 instead of
  silently executing the GET handler and returning 200, every rejection must
  carry an Allow header and a consistent error schema, and OPTIONS preflight
  must be handled rather than returning 404.

  Regression test for bug 6267433.

  Scenario Outline: Read-only endpoints reject unsupported methods with 405
    Given the VST sensor management API is accessible
    When I send a "<method>" request to "<path>"
    Then the API response status is 405
    And the response carries an Allow header
    And the response body uses the consistent error schema

    Examples:
      | method | path                            |
      | POST   | /vst/api/v1/sensor/streams      |
      | POST   | /vst/api/v1/sensor/list         |
      | POST   | /vst/api/v1/sensor/status       |
      | PUT    | /vst/api/v1/sensor/list         |
      | DELETE | /vst/api/v1/sensor/list         |
      | POST   | /vst/api/v1/record/version      |
      | POST   | /vst/api/v1/record/help         |
      | DELETE | /vst/api/v1/record/version      |
      | POST   | /vst/api/v1/storage/version     |
      | POST   | /vst/api/v1/storage/help        |
      | POST   | /vst/api/v1/live/version        |
      | POST   | /vst/api/v1/replay/version      |

  Scenario Outline: OPTIONS preflight is handled, not 404
    Given the VST sensor management API is accessible
    When I send a "OPTIONS" request to "<path>"
    Then the API response status is one of 200, 204, 405
    And the response carries an Allow header

    Examples:
      | path                    |
      | /vst/api/v1/sensor/add  |
      | /vst/api/v1/sensor/list |

  Scenario: GET on a read-only endpoint still succeeds after the fix
    Given the VST sensor management API is accessible
    When I send a "GET" request to "/vst/api/v1/sensor/list"
    Then the API response status is 200

  Scenario: Control - POST on a write-only endpoint already returns 405
    Given the VST sensor management API is accessible
    When I send a "POST" request to "/vst/api/v1/sensor/configuration"
    Then the API response status is 405
