# Third-Party Licenses

This file contains the full license text for all third-party dependencies.

---

## numpy:2.2.6

**License Type:** BSD-3-Clause

**Note:** The wheel ships compiled C extension modules. Python source ships in
`site-packages`; the upstream source is provided as a tarball in
`/usr/share/source/numpy-2.2.6.tar.gz`.

```
Copyright (c) 2005-2024, NumPy Developers.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

    * Redistributions of source code must retain the above copyright
       notice, this list of conditions and the following disclaimer.

    * Redistributions in binary form must reproduce the above
       copyright notice, this list of conditions and the following
       disclaimer in the documentation and/or other materials provided
       with the distribution.

    * Neither the name of the NumPy Developers nor the names of any
       contributors may be used to endorse or promote products derived
       from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

## PyYAML:6.0.2

**License Type:** MIT

**Note:** The wheel ships an optional compiled CYaml C extension. Python
source ships in `site-packages`.

```
Copyright (c) 2017-2021 Ingy döt Net
Copyright (c) 2006-2016 Kirill Simonov

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the
Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

---

## tqdm:4.67.1

**License Type:** MIT / MPL-2.0

**Note:** Pure Python; full source ships in `site-packages/tqdm/`.

```
`tqdm` is a product of collaborative work.
Unless otherwise stated, all authors (see commit logs) retain copyright
for their respective work, and release the work under the MIT licence
(text below).

Exceptions or notable authors are listed below
in reverse chronological order:

* files: *
  MPL-2.0 2015-2024 (c) Casper da Costa-Luis
  [casperdcl](https://github.com/casperdcl).
* files: tqdm/_tqdm.py
  MIT 2016 (c) [noamraph](https://github.com/noamraph)

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the
Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

---

## gcc:14

**License Type:** GPL v3+ with GCC Runtime Library Exception

GCC runtime libraries (`libgcc_s`, `libstdc++`) are inherited from the
NGC distroless Python base. Their full Debian source is included at
`/usr/share/source/` (pulled via `apt-get source gcc-14` during the
builder stage).

---

## Source Code Availability

All source code for open-source components added on top of the NGC
distroless Python base is shipped inside this container (Method 1: source
code in the container). No external network access or NVIDIA-hosted URL is
required for compliance. The source for the approved base container itself
is handled by NGC.

### Pure-Python packages

`tqdm` and `PyYAML` (pure Python portion) are pure Python; their full
source ships in `/usr/local/lib/python3.13/site-packages/`.

### Python packages with vendored native code

`numpy` ships Python source in
`/usr/local/lib/python3.13/site-packages/` and additionally bundles compiled
native C extensions. The corresponding upstream source tarball is
included at:

| File | Component | Upstream tag |
|---|---|---|
| `/usr/share/source/numpy-2.2.6.tar.gz` | `numpy` (C extensions) | `numpy/numpy` `v2.2.6` |

### System libraries inherited from the base

GCC runtime libraries (`libgcc_s`, `libstdc++`) are inherited from the
distroless Python base. Their full Debian source is included at
`/usr/share/source/` (pulled via `apt-get source gcc-14` against
`deb.debian.org/debian trixie{,-updates} main` during the builder stage).

### Retrieving the source

The runtime image is distroless and has no shell, so use `docker create` +
`docker cp` from the host to extract source for inspection:

```
cid=$(docker create <image>:<tag>)
docker cp "$cid":/usr/local/lib/python3.13/site-packages ./python-src
docker cp "$cid":/usr/share/source ./native-src
docker cp "$cid":/app/3rdParty_Licenses.md ./
docker rm "$cid"
```
