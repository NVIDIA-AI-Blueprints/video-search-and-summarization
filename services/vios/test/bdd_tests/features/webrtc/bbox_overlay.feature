Feature: VST BBox Overlay Rendering
  Validate that the overlay engine renders bbox metadata on live pictures,
  replay pictures, and downloaded clips, and that classType filtering is
  case-insensitive.

  # BDD-GAP-050
  @needs_bbox_metadata
  Scenario Outline: classType filter is case-insensitive
    Given the VST API is configured for bbox overlay tests
    And a stream has stored bbox metadata with classType "Person"
    When overlay is requested filtered by classType "<filter_value>"
    Then the overlay is rendered for the filter

    Examples:
      | filter_value |
      | person       |
      | PERSON       |
      | Person       |

  # BDD-GAP-051
  @needs_bbox_metadata
  Scenario: BBox renders on the live picture endpoint
    Given the VST API is configured for bbox overlay tests
    And an active stream has live bbox metadata
    When the live picture is requested with overlay=true
    Then the JPEG contains a region of the expected bbox color

  # BDD-GAP-052
  @needs_bbox_metadata
  Scenario: BBox renders on recorded-stream playback
    Given the VST API is configured for bbox overlay tests
    And a recorded stream has stored bbox metadata
    When the replay picture is requested with overlay=true
    Then the JPEG contains a region of the expected bbox color
