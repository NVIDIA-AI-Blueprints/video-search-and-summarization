Feature: VST Full-File Download Fast Path
  Validate the /api/v1/storage/file fast path that serves a recorded
  file directly (no remux/transcode) when the request asks for the
  whole recording — either explicitly via fullFile=true or implicitly
  via startTime/endTime that span the full file boundaries.

  Background:
    Given a file-based sensor with a known uploaded test recording

  # ---- Positive scenarios -----------------------------------------------------

  Scenario: Download full file via fullFile=true without time range
    When the file is downloaded with fullFile=true and no startTime or endTime
    Then the response is a valid media file
    And a second download with the same parameters returns byte-identical content

  Scenario: Download full file via exact-boundary startTime and endTime
    When the file is downloaded with startTime and endTime equal to the recording's full range
    Then the response is a valid media file
    And a second download with the same parameters returns byte-identical content

  Scenario: Download full file when startTime and endTime are within one frame of the file boundaries
    When the file is downloaded with startTime and endTime nudged inward by one frame
    Then the response is a valid media file
    And a second download with the same parameters returns byte-identical content

  Scenario: URL fast path with fullFile=true returns a symlink-backed URL
    When a temp URL is requested with fullFile=true and no time range
    Then the URL response carries fullFile=true and a non-empty videoUrl
    And the videoUrl serves a valid media file
    And fetching the videoUrl twice returns byte-identical content

  Scenario: Repeated URL requests with fullFile=true reuse the same temp link (cache reuse)
    When a temp URL is requested with fullFile=true twice in a row
    Then both responses return the same videoUrl

  Scenario: Cascade delete removes the temp link when the source recording is deleted
    Given a temp URL was created with fullFile=true
    When the source recording is deleted via the storage DELETE API
    Then the previously issued videoUrl returns 404 Not Found

  # ---- Negative / non-fast-path scenarios -------------------------------------

  Scenario: Sub-range request inside the file does NOT take the fast path
    When the file is downloaded with startTime offset by 1s into the recording and a 2s window
    Then the response is a valid media file
    And the downloaded duration is shorter than the source file's duration

  Scenario: fullFile=true with transcode=full is rejected by the fast path
    When a temp URL is requested with fullFile=true and transcode=full
    Then the URL response does NOT carry fullFile=true

  Scenario: fullFile=true with a container different from the source is rejected by the fast path
    When the file is downloaded with fullFile=true and container=mkv
    Then the response status is not a 200 fast-path response

  Scenario: Download without fullFile=true and without startTime or endTime is rejected with 400
    When the file is downloaded with neither fullFile=true nor a time range
    Then the response status is 400

  Scenario: fullFile=true for an unknown stream returns an error
    When the file is downloaded with fullFile=true for a non-existent streamId
    Then the response status indicates an error
