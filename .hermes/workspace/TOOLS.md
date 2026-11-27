# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

---

## VSS (Video Search & Summarization)

- **Deployment:** whatever `vss configure show` prints. `VSS_PUBLIC_URL` in the
  environment is the origin this sandbox was created for.
- **CLI:** `/usr/local/bin/vss`, also reachable as the `vss_cli` tool.
- **Skills:** `vss-hermes-sync` activates the ones the deployment can serve and
  names the service each inactive one wanted.
