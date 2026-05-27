Feature: VST Replay Stream Query API Validation
  Validate that the query API returns valid timestamp data during active replay WebRTC streaming

  Scenario: Query all active replay streams for current timestamp
    Given the VST API and WebSocket are configured for query test
    When all replay streams with valid timelines are selected for query test
    And each WebRTC replay session is started and queried
    Then each query response contains a valid timestamp

  Scenario: Query all active peer connections without peerid
    Given the VST API and WebSocket are configured for query test
    When all replay streams with valid timelines are selected for query test
    And all WebRTC replay sessions are started and the query API is called without peerid
    Then the query response is a valid array containing all active peer connections

  Scenario: Query paused replay streams returns consistent timestamp across both query variants
    Given the VST API and WebSocket are configured for query test
    When all replay streams with valid timelines are selected for query test
    And the replay streams are started paused and queried with both query variants
    Then the ts from peerid query matches the frameTime from the array query for each stream
