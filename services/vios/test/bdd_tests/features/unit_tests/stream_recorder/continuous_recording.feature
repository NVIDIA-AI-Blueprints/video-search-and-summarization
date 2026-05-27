Feature: VST Stream Recorder - Continuous (alwaysOn) Recording
  Validate that always-on recording produces gap-free timelines, starts
  promptly when a sensor comes online, and that the reported size matches
  the sum of on-disk segment sizes.

  # BDD-GAP-060
  @longrun
  Scenario: Always-on recording produces gap-free timelines for 1 hour
    Given an always-on RTSP sensor exists for continuous recording
    When the sensor records continuously for 60 minutes
    Then the merged timeline has no gap longer than 1 second

  # BDD-GAP-061
  Scenario: Always-on starts within 1s of sensor going online
    Given an always-on sensor exists for cold-start measurement
    And the sensor is currently offline
    When the sensor comes online
    Then a recording segment for the sensor appears within 1 second

  # BDD-GAP-062
  Scenario: Recording size matches the sum of MP4 sizes on disk
    Given an always-on RTSP sensor exists with ongoing recording
    When the recording size is queried and the on-disk segment sizes are summed
    Then the two values match within 1 percent
