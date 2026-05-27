Feature: VST Video Wall Multi-Tile Streaming
  Validate that the video-wall view can render multiple sensors concurrently,
  that pausing one tile does not affect the others, and that a 12-tile wall
  remains stable over a long run.

  # BDD-GAP-047
  Scenario: Video wall renders all selected sensors
    Given the VST API is configured for video-wall tests
    And at least 6 live streams are available
    When 6 concurrent WebRTC tiles are opened
    Then each tile delivers at least 5 fps for 30 seconds

  # BDD-GAP-048
  Scenario: Single-tile pause does not affect other tiles
    Given the VST API is configured for video-wall tests
    And a 6-tile wall is streaming
    When tile 1 is paused
    Then tile 1 stops receiving new frames
    And tiles 2 through 6 keep delivering frames

  # BDD-GAP-049
  @longrun
  Scenario: 12-tile wall is stable for 30 minutes
    Given the VST API is configured for video-wall tests
    And at least 12 live streams are available
    When 12 concurrent WebRTC tiles run for 30 minutes
    Then livestream-ms PID remains unchanged
    And no tile drops frames for more than 5 seconds
