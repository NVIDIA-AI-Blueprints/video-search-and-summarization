Feature: VST RTSP Proxy Stream Service API Unit Tests
  Validate that the RTSP Proxy Stream Service REST APIs respond correctly.

  Scenario: Get RTSP proxy service version
    Given the VST RTSP proxy API is accessible
    When I request the RTSP proxy service version
    Then the proxy response status is 200
    And the proxy response contains a device type and build version

  Scenario: Get list of available proxy streams
    Given the VST RTSP proxy API is accessible
    When I request the list of proxy streams
    Then the proxy response status is 200
    And the proxy response is a valid JSON array

  Scenario: Get RTSP proxy service configuration
    Given the VST RTSP proxy API is accessible
    When I request the RTSP proxy service configuration
    Then the proxy response status is 200
    And the proxy response contains configuration fields

  Scenario: Get RTSP proxy info
    Given the VST RTSP proxy API is accessible
    When I request the RTSP proxy info
    Then the proxy response status is 200
    And the proxy info contains server entries and stats
