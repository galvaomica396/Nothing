# Third-Party Notices

Nothing includes or depends on third-party software and public data. These
notices do not replace the license texts distributed by each upstream project.

## Python PDF libraries

### PyMuPDF and PyMuPDF4LLM

- License: GNU Affero General Public License v3.0, or a separate Artifex
  commercial license obtained from the copyright holder
- Project: https://pymupdf.readthedocs.io/
- Use in Nothing: PDF parsing, rendering, text extraction, and redaction

### pypdf

- License: BSD 3-Clause License
- Project: https://pypdf.readthedocs.io/
- Use in Nothing: PDF inspection and validation

## Frontend and desktop runtime

### pdfjs-dist

- License: Apache License 2.0
- Project: https://github.com/mozilla/pdf.js
- Use in Nothing: in-app PDF canvas rendering

### Tauri

- License: MIT License or Apache License 2.0
- Project: https://tauri.app/
- Use in Nothing: desktop application runtime and native bridge

## Korean privacy detector

### ko-pii 1.15.2

- Revision: `635ade22cfe8d89761ed0e8948b5470e2307506e`
- License: MIT License
- Project: https://github.com/Marker-Inc-Korea/ko-pii

Copyright (c) 2026 Marker Inc.

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

The public-data source and transformation metadata must remain with redistributed
copies of the generated dataset.
