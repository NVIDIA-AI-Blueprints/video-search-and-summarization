@ui
Feature: Video player fullscreen controls
  The controls for a live video remain usable when the player enters fullscreen.

  Scenario: Fullscreen controls auto-hide and remain usable
    Given the VIOS live-stream page has a video player
    When I enter fullscreen from the video player controls
    Then the video player is the browser fullscreen element
    And all five live-stream controls are available in the fullscreen player
    When I open the fullscreen quality dropdown
    Then all quality options are visible
    And the fullscreen controls are visible
    When I close the fullscreen quality dropdown
    Then the quality options are hidden
    When I leave the pointer idle over the fullscreen player
    Then the fullscreen controls are hidden
    When I move the pointer over the fullscreen player
    Then the fullscreen controls are visible
    When I exit fullscreen from the video player controls
    Then the browser leaves fullscreen mode
