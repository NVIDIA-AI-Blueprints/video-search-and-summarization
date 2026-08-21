@ui
Feature: Live video delivery protocol selector
  The live player keeps WebRTC as the default and can switch cleanly to MPEG-DASH.

  Scenario: Switch a live player from WebRTC to DASH and back
    Given the VIOS live-stream page has a DASH-capable video player
    Then WebRTC is the selected delivery protocol
    When I switch the live player to DASH
    Then the DASH player reports its first frame
    And WebRTC-only quality controls are hidden
    When I switch the live player back to WebRTC
    Then the DASH viewer lease is released
