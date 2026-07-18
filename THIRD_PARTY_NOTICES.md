# Third-Party Notices

Nothing includes or depends on third-party software and public data. The
project license is in `LICENSE`; the complete Apache License 2.0 text used by
the Apache-licensed dependencies below is distributed as
`LICENSE-APACHE-2.0.txt`.

The dependency versions and attributions below were checked against the
installed npm packages, Cargo crates, and Python distributions used to build
Nothing 4.6.3. No installed Apache-licensed direct dependency supplied an
additional `NOTICE` file.

## Python PDF libraries

### PyMuPDF and PyMuPDF4LLM

- License: GNU Affero General Public License v3.0, or a separate Artifex
  commercial license obtained from the copyright holder
- Copyright holder identified by the installed PyMuPDF package: Artifex
- Project: https://pymupdf.readthedocs.io/
- Use in Nothing: PDF parsing, rendering, text extraction, and redaction

The installed PyMuPDF distribution identifies its terms as “Dual Licensed -
GNU AFFERO GPL 3.0 or Artifex Commercial License.” The complete AGPL-3.0 text
is distributed in `LICENSE`.

### pypdf

- License: BSD 3-Clause License
- Project: https://pypdf.readthedocs.io/
- Use in Nothing: PDF inspection and validation

Copyright (c) 2006-2008, Mathieu Fenniak
Some contributions copyright (c) 2007, Ashish Kulkarni <kulkarni.ashish@gmail.com>
Some contributions copyright (c) 2014, Steve Witham <switham_github@mac-guyver.com>

All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice,
  this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.
* The name of the author may not be used to endorse or promote products
  derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

## Frontend runtime

### React and React DOM 19.2.7

- License: MIT License
- Project: https://react.dev/
- Use in Nothing: application UI and DOM rendering

Copyright (c) Meta Platforms, Inc. and affiliates.

### pdfjs-dist 5.7.284

- License: Apache License 2.0
- Project: https://github.com/mozilla/pdf.js
- Use in Nothing: in-app PDF canvas rendering
- Complete license text: `LICENSE-APACHE-2.0.txt`

The installed `pdfjs-dist` package contains no separate copyright line or
`NOTICE` file. Its `LICENSE` file is copied verbatim to
`LICENSE-APACHE-2.0.txt`.

## Desktop runtime

### Tauri and @tauri-apps packages

- Components: Tauri 2.11.1, `@tauri-apps/api` 2.11.0,
  `tauri-plugin-opener` and `@tauri-apps/plugin-opener` 2.5.4
- License: MIT License or Apache License 2.0
- Project: https://tauri.app/
- Use in Nothing: desktop application runtime, native bridge, and system
  file/URL opening
- Complete Apache license text: `LICENSE-APACHE-2.0.txt`

Copyright (c) 2017 - Present Tauri Apps Contributors

The plugin SPDX metadata also records:
Copyright 2019-2022, The Tauri Programme in the Commons Conservancy.

### serde and serde_json

- Components: serde 1.0.228 and serde_json 1.0.149
- License: MIT License or Apache License 2.0
- Project: https://serde.rs/
- Use in Nothing: typed IPC and JSON serialization
- Complete Apache license text: `LICENSE-APACHE-2.0.txt`

The installed crates identify Erick Tryzelaar and David Tolnay as authors.
Their `LICENSE-MIT` files contain the MIT grant below but no separate
copyright line; no copyright notice has been invented here.

### rfd 0.15.4

- License: MIT License
- Project: https://github.com/PolyMeilex/rfd
- Use in Nothing: native file and folder dialogs

Copyright (c) 2022 Bartłomiej Maryńczak

## Korean privacy detector

### ko-pii 1.15.2

- Revision: `635ade22cfe8d89761ed0e8948b5470e2307506e`
- License: MIT License
- Project: https://github.com/Marker-Inc-Korea/ko-pii
- Use in Nothing: Korean privacy-entity detection

Copyright (c) 2026 Marker Inc.

## MIT License text

The following MIT permission notice applies to React, React DOM, the MIT
option for Tauri and its listed packages, the MIT option for serde and
serde_json, rfd, and ko-pii. Each upstream copyright or author attribution is
listed in the corresponding section above.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Korean administrative-region data

- File: `data/kr_regions.json`
- Source: 행정표준코드관리시스템 법정동코드 전체자료
- Source page: https://www.code.go.kr/stdcode/regCodeL.do
- Source identifier recorded in the data file:
  `official-code.go.kr:법정동코드전체자료`
- Generated copy: `legal_dong_code_full_2026-03-26_cp949.txt`, transformed on
  2026-05-30; the source checksum and record counts remain in the JSON metadata

The public-data source and transformation metadata must remain with
redistributed copies of the generated dataset.
