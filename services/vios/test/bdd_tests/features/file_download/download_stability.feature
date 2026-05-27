Feature: VST Video Download - Stability Under Load
  Validate that storage-ms remains stable under sustained download load
  and that non-blocking URL generation returns promptly.

  # BDD-GAP-026
  @longrun
  Scenario: 100 sequential blocking downloads over 2 hours all return valid MP4
    Given the VST API is configured for stability tests
    And the storage has at least one stream with sufficient recording
    When 100 sequential blocking download requests are issued
    Then all 100 downloads return valid MP4
    And no storage-ms container restarts are observed

  # BDD-GAP-027
  Scenario: Non-blocking download URL is returned within 2 seconds
    Given the VST API is configured for stability tests
    And the storage has at least one stream with sufficient recording
    When a non-blocking download URL is requested for a 1 hour clip
    Then the URL response returns within 2 seconds
    And the returned URL eventually serves the clip

  # BDD-GAP-028
  @longrun
  Scenario: storage-ms survives 2 hours of heavy parallel download load
    Given the VST API is configured for stability tests
    And the storage has at least one stream with sufficient recording
    When 10 parallel download workers run for 2 hours
    Then all workers complete
    And storage-ms PID remains unchanged across the run
