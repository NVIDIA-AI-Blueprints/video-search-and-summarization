<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Cross-skill conventions

For skills running in OpenClaw / NemoClaw and bridged to chat channels (Slack, Telegram, Discord).

## Media attachments

To attach a video, image, or PDF to the reply, put a `MEDIA:` line on its own line, outside any code fence. Local paths and HTTP URLs both work:

```
MEDIA: <path-to-local-file>
MEDIA: https://host.example.com/clip.mp4
```

Save artifacts to the working directory — do not hard-code `/tmp/...` or any other absolute path. Files outside the working directory may not be reachable by the runtime that delivers the attachment.

Default to plain links / markdown links in tables. Use `MEDIA:` when the user asks to "show / render / post" something, or when the user needs to see the visual to make a decision.

The runtime delivers `MEDIA:` local paths as native attachments. Do not upload skill-generated artifacts (charts, reports, clips you produced) to VST or any other host to get a URL — emit the local path directly. Uploading is only correct when the URL already exists on a serving host (e.g. `screenshot_url` from a search result).

## Output style

- Tables: one row per record. Keep cells short; place longer narrative below the table.
- Links in tables: use `[label](URL)` markdown directly in the cell (e.g. `[clip](https://...)`, `[snapshot](https://...)`). Slack, Discord, the browser TUI, and GitHub markdown all render this. The cell text becomes the clickable link. Do not use HTML anchor tags (`<a id="...">`) or any other inline HTML.
- Limit user-facing messages to a single acknowledgement before long work and the final response. Keep intermediate tool calls and reasoning internal; do not narrate them.
- Do not narrate what you are about to do or refer to the user in third person. Just take the action. Examples to avoid: "I'll present candidate #1 with an overlay clip so the user can confirm…", "Now I'll show the next candidate…", "Let me fetch the chart and post it…". Replace these with the action itself — post the clip and ask the question, post the chart, etc.
