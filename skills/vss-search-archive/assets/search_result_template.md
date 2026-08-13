# Video Search Results

For each hit, report its exact source, start and end times, similarity score,
complete media URL, verification result, and verification criteria when
present. Do not include raw JSON.

Similarity scores are retrieval evidence; the separate verification result
records whether the bounded clip satisfied the visual request.

Include the following section only when the nonempty displayed result set is
entirely `unverified`. Omit it if any displayed hit is `confirmed` or
`rejected`.

## Verification Step

Would you like me to verify the unverified search results?
