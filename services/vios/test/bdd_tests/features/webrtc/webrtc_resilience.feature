Feature: VST WebRTC and Live-Picture Resilience
  Validate that live WebRTC sessions recover from transient network outages,
  that the picture API survives concurrent bursts, and that replay/live mode
  transitions and parallel-load scenarios behave correctly.

  # BDD-GAP-041
  @needs_iptables
  Scenario: Active WebRTC session reconnects after a transient network break
    Given the VST API is configured for resilience tests
    And an active live WebRTC session is established
    When the WebRTC network port is blocked for 5 seconds and then unblocked
    Then frames resume within 10 seconds of network restore
    And the session ID is retained

  # BDD-GAP-042
  Scenario: 100 concurrent picture API calls do not crash livestream-ms
    Given the VST API is configured for resilience tests
    And at least one active live stream exists
    When 100 parallel GET picture requests are issued for the same stream
    Then 100 of the requests return 200
    And livestream-ms PID is unchanged

  # BDD-GAP-043
  Scenario: Replay WebRTC stream resumes playback after seek
    Given the VST API is configured for resilience tests
    And an active replay WebRTC session is paused at T0
    When the replay session is seeked to T0+30s and resumed
    Then the replay session reports frame flow with non-zero fps within 5 seconds

  # BDD-GAP-044
  Scenario: Replay playback speed change is actually applied
    Given the VST API is configured for resilience tests
    And an active replay WebRTC session is playing at 1x
    When playback speed is set to 2x
    Then the measured frame inter-arrival time is approximately half the original

  # BDD-GAP-045
  Scenario: Live stream survives a parallel video URL download
    Given the VST API is configured for resilience tests
    And an active live WebRTC session is established
    When a blocking video download is triggered for a different sensor
    Then the live session keeps delivering frames during and after the download

  # BDD-GAP-046
  Scenario: Switching from recorded to live mode succeeds without error
    Given the VST API is configured for resilience tests
    And an active replay WebRTC session is established
    When the session is switched to live mode
    Then within 5 seconds a live session starts
    And the replay session is closed gracefully
