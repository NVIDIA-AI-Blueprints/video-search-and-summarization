Feature: VST Video Download - API Contracts (Negative + Boundary)
  Validate that the download API enforces input validation and clips
  returned MP4s to the requested time window.

  # BDD-GAP-017
  Scenario: Download with startTime greater than endTime returns 4xx
    Given the VST API is configured for download contract tests
    And a test video has been uploaded for download contract tests
    When a download is requested with startTime greater than endTime
    Then the download response is a 4xx with a structured error
    And no temp clip is written for the reversed range

  # BDD-GAP-018
  Scenario Outline: Download with invalid fullLength value is rejected
    Given the VST API is configured for download contract tests
    And a test video has been uploaded for download contract tests
    When a download is requested with fullLength "<bad_value>"
    Then the download response is a 4xx with a structured error

    Examples:
      | bad_value |
      | -1        |
      | abc       |
      | null      |

  # BDD-GAP-020
  Scenario: Intermediate-timeline download returns only that interval
    Given the VST API is configured for download contract tests
    And a test video with a known long recording exists
    When a download is requested for an intermediate sub-window
    Then the returned MP4 duration matches the requested sub-window within tolerance
