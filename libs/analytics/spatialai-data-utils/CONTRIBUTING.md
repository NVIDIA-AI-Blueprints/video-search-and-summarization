# Contributing

This project will only accept contributions under the Apache-2.0 license.

By submitting a contribution (a patch, pull/merge request, or any other change)
you certify that you have the right to license the contribution under the
[Apache License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0), and
that you do so. NVIDIA may include your contribution in this project under that
license.

## File-level license headers

If your contribution adds or modifies a source file, please:

- Add or update the SPDX file header so that it reflects the correct copyright
  holders and license identifier.
- For new files authored by you (or your employer) on behalf of this project,
  use the SPDX identifier `Apache-2.0` and add the appropriate copyright
  notice.
- For changes to files derived from third-party sources, preserve the existing
  third-party copyright and license notices. The nuScenes dev-kit-derived files
  and TrackEval-derived files are listed in [`NOTICE`](NOTICE). List the file(s)
  you modified in your change description, and keep the existing dual-license
  SPDX identifier (e.g. `MIT AND Apache-2.0`).
- Do not introduce code under licenses that are incompatible with Apache-2.0
  distribution. If you need to vendor third-party code, including code derived
  from the nuScenes dev-kit or TrackEval, raise it in your change description
  so the appropriate notice can be added to [`NOTICE`](NOTICE) and
  [`3rdParty_Licenses.md`](3rdParty_Licenses.md).

## Sign your work

We require that all contributors "sign off" on every commit they author. This
certifies that the contribution is your original work, or you have rights to
submit it under the same license, or a compatible license. Any contribution
which contains commits that are not signed off will not be accepted.

To sign off on a commit you simply use the `--signoff` (or `-s`) option when
committing your changes:

```bash
git commit -s -m "Add cool feature."
```

This will append the following to your commit message:

```
Signed-off-by: Your Name <your@email.com>
```

The name and email used to sign off must match a real identity (typically the
name and email associated with your `git config user.name` /
`git config user.email`); pseudonyms or anonymous contributions cannot be
accepted.

By signing off on a commit, you certify the below Developer Certificate of
Origin (DCO):

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```
