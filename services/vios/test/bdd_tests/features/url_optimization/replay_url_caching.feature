Feature: Replay Picture and Video URL Caching Optimization
  Validate that repeated requests for the same replay picture URL or video URL
  return a cached response instead of regenerating the file, and that the expiry
  time is extended on cache hits

  Scenario: Replay picture URL returns cached result on repeated request
    Given the VST API is configured for URL caching test
    When the list of available replay streams is fetched
    And the recording timelines are fetched for URL caching test
    And a valid replay timestamp is selected
    Then a replay picture URL is requested
    And the same replay picture URL is requested again
    And the second picture URL response reuses the cached file
    And the picture URL expiry time is refreshed on cache hit

  Scenario: Video URL returns cached result on repeated blocking request
    Given the VST API is configured for URL caching test
    When the list of available replay streams is fetched
    And the recording timelines are fetched for URL caching test
    And a valid video time range is selected
    Then a blocking video URL is requested
    And the same blocking video URL is requested again
    And the second video URL response reuses the cached file
    And the video URL expiry time is refreshed on cache hit
