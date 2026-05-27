Feature: VST Sensor Add — RTSP DESCRIBE pre-flight verification
  Validate the opt-in verifyRtsp flag on POST /vst/api/v1/sensor/add. When the
  flag is true and the supplied RTSP URL is unreachable, the API must reject
  the request and must NOT persist the sensor. When the flag is false (or
  omitted), the existing behaviour is preserved (the sensor is accepted) so
  legacy clients are not broken.

  Background:
    Given the VST sensor management API is accessible

  Scenario: Reject add-sensor when verifyRtsp is true and the RTSP URL is unreachable
    When I POST to sensor/add with an unreachable RTSP URL and verifyRtsp set to true
    Then the sensor add response status is 4xx
    And the unreachable sensor is not present in /sensor/list

  Scenario: Accept add-sensor when verifyRtsp is omitted (default false), preserving legacy behaviour
    When I POST to sensor/add with an unreachable RTSP URL and no verifyRtsp flag
    Then the sensor add response status is 200
    And I clean up the added sensor

  Scenario: Accept add-sensor when verifyRtsp is explicitly false
    When I POST to sensor/add with an unreachable RTSP URL and verifyRtsp set to false
    Then the sensor add response status is 200
    And I clean up the added sensor
