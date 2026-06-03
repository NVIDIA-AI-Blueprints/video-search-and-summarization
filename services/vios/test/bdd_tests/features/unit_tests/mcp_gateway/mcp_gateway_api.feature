Feature: VST MCP Gateway Service API Unit Tests
  Validate that the MCP Gateway tools respond correctly
  when called via the MCP protocol.

  Scenario: List all sensors
    Given the MCP gateway is accessible
    When I call the sensor_list tool
    Then the MCP response is valid JSON
    And the MCP response contains sensor data

  Scenario: Get sensor status
    Given the MCP gateway is accessible
    When I call the sensor_status tool
    Then the MCP response is valid JSON

  Scenario: Check sensor health
    Given the MCP gateway is accessible
    When I call the sensor_health_check tool
    Then the MCP response is valid JSON
    And the MCP response contains a status field

  Scenario: Trigger sensor scan
    Given the MCP gateway is accessible
    When I call the sensor_scan tool
    Then the MCP response is valid JSON

  Scenario: Get sensor status by ID
    Given the MCP gateway is accessible
    And at least one sensor is available via MCP
    When I call the sensor_status_by_id tool for the first sensor
    Then the MCP response is valid JSON

  Scenario: Get sensor info by ID
    Given the MCP gateway is accessible
    And at least one sensor is available via MCP
    When I call the sensor_info_by_id tool for the first sensor
    Then the MCP response is valid JSON

  Scenario: Get sensor settings by ID
    Given the MCP gateway is accessible
    And at least one sensor is available via MCP
    When I call the sensor_settings_by_id tool for the first sensor
    Then the MCP response is valid JSON

  Scenario: Get recording status for a stream
    Given the MCP gateway is accessible
    And at least one stream is available via MCP
    When I call the record_stream_status tool for the first stream
    Then the MCP response is valid JSON

  Scenario: Get recording timelines for a stream
    Given the MCP gateway is accessible
    And at least one stream is available via MCP
    When I call the record_stream_timelines tool for the first stream
    Then the MCP response is valid JSON

  Scenario: Start and stop recording for a stream
    Given the MCP gateway is accessible
    And at least one stream is available via MCP
    When I call the record_stream_start tool for the first stream
    Then the MCP response is valid JSON
    When I call the record_stream_stop tool for the first stream
    Then the MCP response is valid JSON

  Scenario: Get live picture as base64 for a stream
    Given the MCP gateway is accessible
    And at least one stream is available via MCP
    When I call the get_live_picture_base64 tool for the first stream
    Then the MCP response is valid JSON
    And the MCP response contains base64 image data

  Scenario: Get live picture URL for a stream
    Given the MCP gateway is accessible
    And at least one stream is available via MCP
    When I call the get_live_picture_url tool for the first stream
    Then the MCP response is valid JSON
    And the MCP response contains an image URL

  Scenario: List all storage files
    Given the MCP gateway is accessible
    When I call the storage_file_list tool
    Then the MCP response is valid JSON

  Scenario: List storage files by sensor
    Given the MCP gateway is accessible
    And at least one sensor is available via MCP
    When I call the storage_file_list_by_sensor tool for the first sensor
    Then the MCP response is valid JSON

  Scenario: Get storage file paths by sensor
    Given the MCP gateway is accessible
    And at least one sensor is available via MCP
    When I call the storage_file_path_by_sensor tool for the first sensor
    Then the MCP response is valid JSON
