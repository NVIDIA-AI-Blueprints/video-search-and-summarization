Feature: VST REST endpoints declare JSON Content-Type for JSON bodies
  Regression for bug 6216242. The shared HTTP response-emission framework
  writes valid JSON bodies (objects, arrays, primitives) but stamps every
  response with "Content-Type: text/plain" instead of "application/json".
  This breaks OpenAPI-generated SDKs, swagger contract validators, and any
  content-type-routed tooling. Every JSON-body endpoint across all reachable
  microservices must advertise an application/json content type.

  Scenario Outline: JSON-body endpoints advertise application/json
    Given the VST REST API is accessible
    When I request the JSON endpoint "<path>"
    Then the endpoint response status is 200
    And the endpoint response body parses as JSON
    And the endpoint response Content-Type is application/json

    Examples: version endpoints (JSON body) per microservice
      | path                          |
      | /vst/api/v1/sensor/version    |
      | /vst/api/v1/record/version    |
      | /vst/api/v1/live/version      |
      | /vst/api/v1/replay/version    |
      | /vst/api/v1/storage/version   |

    Examples: help endpoints (JSON array body) per microservice
      | path                          |
      | /vst/api/v1/sensor/help       |
      | /vst/api/v1/record/help       |
      | /vst/api/v1/live/help         |
      | /vst/api/v1/replay/help       |
      | /vst/api/v1/storage/help      |
