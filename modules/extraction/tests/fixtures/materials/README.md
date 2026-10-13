Files the tests of an uploaded material's readers read
(`tests/unit/test_material_formats.py`). Every value in them is invented.

- `poi/` -- nine files Microsoft Office wrote, from the Apache POI project's
  test data (Apache-2.0). See `poi/NOTICE.md`.
- `pdf/` -- twelve PDFs made for these tests by printing invented pages from
  Chrome: written pages, pages that are only a picture, a picture with a few
  characters beside it, a marking at the top, at the foot and in the body.
- `xls/` -- three old workbooks made for these tests with xlwt 1.3.0: a
  contact sheet with a hidden second sheet, and two that carry a marking on
  the first sheet and on the second.
- `openpyxl/` -- one workbook made for these tests with openpyxl 3.1.5: two
  sheets with a cell note each. openpyxl names a sheet's notes
  `xl/comments/comment1.xml`, where Excel names them `xl/comments1.xml`.
