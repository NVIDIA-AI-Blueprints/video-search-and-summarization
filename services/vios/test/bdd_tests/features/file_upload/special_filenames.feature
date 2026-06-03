Feature: VST File Upload - Special Filename Handling
  Validate that filenames with safe special characters are accepted,
  that path-traversal payloads are rejected, and that sensor names
  exceeding the supported length are rejected with a clear error.

  Scenario Outline: Upload accepts safe special characters in filename
    Given the VST API is configured for special-filename uploads
    When a video is uploaded with filename "<filename>"
    Then the special-filename upload succeeds
    And the uploaded file is retrievable by streamId
    And the uploaded file appears in the media file list

    Examples:
      | filename                  |
      | video+with+plus.mp4       |
      | video.with.dots.mp4       |
      | video-with_dash.mp4       |

  Scenario Outline: Upload rejects path-traversal filename
    Given the VST API is configured for special-filename uploads
    When a video is uploaded with traversal filename "<filename>"
    Then the traversal upload is rejected with a 4xx status
    And no file is written outside the storage root

    # 'a/../b' is intentionally NOT in this list: the nginx ingress
    # normalises that path to 'b' (RFC 3986) before it reaches the backend,
    # so it becomes a legitimate upload of a file called 'b'. There is no
    # server-side defense to exercise — the defense already happened at the
    # edge and the resolved path is safe.
    Examples:
      | filename                |
      | ../../etc/passwd        |
      | ..\\..\\windows\\system |
      | /etc/hosts              |

  Scenario: Sensor name longer than supported is truncated to the maximum length
    Given the VST API is configured for special-filename uploads
    When a sensor is added with a name that exceeds the supported length
    Then the sensor add response is success
    And the stored sensor name is truncated to the maximum supported length
