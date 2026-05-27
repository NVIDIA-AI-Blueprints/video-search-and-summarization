Feature: VST URL Expiry Enforcement
  Validate that picture URL expiry is enforced server-side and that
  invalid expiryMinutes values are rejected at request time.

  Scenario: Picture URL returns 404/410 after expiryISO
    Given the VST API is configured for URL expiry tests
    And the recording timelines are fetched for URL expiry tests
    And a valid replay timestamp is selected for expiry test
    When a picture URL is requested with expiryMinutes 1
    And the picture URL is fetched after the expiry passes
    Then the picture URL response status is 404 or 410

  Scenario Outline: Negative or zero expiryMinutes is rejected
    Given the VST API is configured for URL expiry tests
    And the recording timelines are fetched for URL expiry tests
    And a valid replay timestamp is selected for expiry test
    When a picture URL is requested with expiryMinutes "<bad_value>"
    Then the picture URL request is rejected with 4xx

    Examples:
      | bad_value |
      | -1        |
      | 0         |
