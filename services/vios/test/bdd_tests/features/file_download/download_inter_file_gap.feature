Feature: VST Video Download Inter-File Gap End-Time Handling
  Validate end-time handling of the storage download API around recording-file
  boundaries. Recording filenames are epoch-ms timestamps of the first frame.
  There is always an inter-frame gap between one file's last frame and the next
  file's first frame.

  Scenario: End time falls in the inter-file gap before a recorded file
    Given the VST API is configured
    And an RTSP sensor with at least two recorded files is selected
    When a non-first recorded file is picked at random as the boundary file
    And the storage download API is called with end time 10 ms before the boundary file start and start time 10 seconds before that
    Then the result file end time is strictly less than the boundary file start time

  Scenario: End time exactly equals the boundary file start time
    Given the VST API is configured
    And an RTSP sensor with at least two recorded files is selected
    When a non-first recorded file is picked at random as the boundary file
    And the storage download API is called with end time equal to the boundary file start and start time 10 seconds before that
    Then the result file end time is greater than or equal to the requested end time
