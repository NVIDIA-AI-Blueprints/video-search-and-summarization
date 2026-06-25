Feature: VST Replay WebRTC Stream Validation
  Validate that replay WebRTC streams can be established and deliver healthy video
  from VST replay API using WebSocket signaling

  Scenario: Validate replay WebRTC stream establishment and health
    Given the VST API and WebSocket are configured
    When the list of available replay streams is fetched
    And valid time ranges from the timelines are selected
    Then a WebRTC connection is established for each replay stream
    And the stream reaches PLAYING state
    And the video framerate is healthy

