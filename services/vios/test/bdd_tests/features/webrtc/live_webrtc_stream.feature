Feature: VST Live WebRTC Stream Validation
  Validate that live WebRTC streams can be established and deliver healthy video
  from VST live API using WebSocket signaling

  Scenario: Validate WebRTC stream establishment and health
    Given the VST API and WebSocket are configured
    When the list of available live streams is fetched
    Then a WebRTC connection is established for a stream
    And the stream reaches PLAYING state
    And the video framerate is healthy

