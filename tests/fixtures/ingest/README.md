# Fixtures for tests/test_ingest_extract.py

Small files, built once from the six-line Persian sample (`SAMPLE` in the test
file: two headings, eight ZWNJ, the digits ۹ ۱۸ ۱۵ ۲٬۵۰۰٬۰۰۰, one sentence
with Arabic ي and ك, and an English email). Hostile inputs that would be large
(zip bombs, 20 MiB bodies, 5,001-row sheets) are built inside the tests instead.

| File | Made with | Used for |
|------|-----------|----------|
| `fa-sample.docx` | stdlib `zipfile`, hand-written `word/document.xml` (`Heading2` style on the two headings) | DOCX extraction, SC-001 |
| `fa-sample.xlsx` | `openpyxl` 3.1.5, header row `عنوان` / `پاسخ` | XLSX extraction |
| `fa-sample.csv` | the same rows as the XLSX, UTF-8 without BOM | CSV extraction |
| `fa-sample-cp1256.txt` | the sample as TXT, saved as cp1256 after `ی`→`ي`, no U+0654, ASCII digits (cp1256 has none of those) | REQ-004 step 3 |
| `fa-sample.pdf` | the sample as HTML with Vazirmatn, printed by headless Google Chrome 154 (`--print-to-pdf`) | SC-002 |
| `encrypted.pdf` | a hand-written one-page Helvetica PDF, encrypted with a user password (RC4-128) by pypdf 6.19.0 in a throwaway venv; pypdf is not a project dependency | H5 |
| `lol.docx` | the same minimal DOCX parts as `fa-sample.docx`, with a nine-level billion-laughs DTD in `word/document.xml` | H2 |
| `lol.xlsx` | an `openpyxl` workbook whose `xl/worksheets/sheet1.xml` was replaced by the same DTD | H3 |
