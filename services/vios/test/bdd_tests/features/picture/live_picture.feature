Feature: VST Live Picture Validation
  Validate that live pictures retrieved from VST live API are valid JPEG images
  when requested in parallel for multiple streams

  Scenario: Validate live pictures with parallel requests
    Given the VST API is configured
    When the list of available live streams is fetched
    Then live pictures for each stream are fetched in parallel
    And all fetched pictures are valid JPEG images

