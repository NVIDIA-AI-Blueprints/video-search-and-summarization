Feature: VST Sensor Add — duplicate-sensor rejection reports the conflicting check
  Rejections from POST /vst/api/v1/sensor/add caused by another sensor with the
  same RTSP URL, IP, or name must surface the conflict in the error_message AND
  as structured JSON fields (existingSensorId / existingSensorName) so clients
  can recover programmatically without re-fetching /sensor/list or regex-parsing
  the free-form error_message.
  A URL/IP conflict (including the case where the name also collides) is
  reported as "Sensor exists already"; a pure name collision is reported as
  "User given name is invalid or already exists".

  Background:
    Given the VST sensor management API is accessible

  Scenario: Re-adding the same RTSP URL with a different name reports the URL conflict
    Given I have added an RTSP sensor and captured its identity
    When I POST to sensor/add with the same RTSP URL but a different name
    Then the sensor add response status is 4xx
    And the response error_message contains "Sensor exists already"
    And the response includes existingSensorId matching the first sensor
    And the response includes existingSensorName matching the first sensor
    And I clean up the first added sensor

  Scenario: Re-adding the same RTSP URL with the same name reports the URL conflict
    Given I have added an RTSP sensor and captured its identity
    When I POST to sensor/add with the same RTSP URL and the same name
    Then the sensor add response status is 4xx
    And the response error_message contains "Sensor exists already"
    And the response includes existingSensorId matching the first sensor
    And the response includes existingSensorName matching the first sensor
    And I clean up the first added sensor

  Scenario: Adding with a different URL but a name already in use reports the name conflict
    Given I have added an RTSP sensor and captured its identity
    When I POST to sensor/add with a different RTSP URL but the same name
    Then the sensor add response status is 4xx
    And the response error_message contains "User given name is invalid or already exists"
    And the response includes existingSensorId matching the first sensor
    And the response includes existingSensorName matching the first sensor
    And I clean up the first added sensor
