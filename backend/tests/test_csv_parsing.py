"""Tests for parsers.parse_csv (BOM handling, malformed values, 400s not 500s)."""
import unittest

from fastapi import HTTPException

import parsers

GOOD_HEADER = (
    "Title,Author,ISBN13,My Rating,Average Rating,Number of Pages,"
    "Original Publication Year,Date Read,Exclusive Shelf,My Review\n"
)
GOOD_ROW = '"Book One","Author One","=""9780000000001""",5,4.25,300,2020,2021/05/01,read,"Loved it"\n'


class CSVParsingTests(unittest.TestCase):
    def test_parses_utf8_bom_bytes(self):
        # encode(...) with the utf-8-sig codec prepends the BOM bytes itself;
        # manually concatenating a literal "\ufeff" would double it up.
        content = (GOOD_HEADER + GOOD_ROW).encode("utf-8-sig")
        books = parsers.parse_csv(content)
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["title"], "Book One")
        self.assertEqual(books[0]["author"], "Author One")
        self.assertEqual(books[0]["isbn"], "9780000000001")
        self.assertEqual(books[0]["my_rating"], 5)
        self.assertEqual(books[0]["avg_rating"], 4.25)
        self.assertEqual(books[0]["num_pages"], 300)

    def test_parses_utf8_bom_str_defensively(self):
        # Defends against a BOM surviving an upstream .decode("utf-8") that
        # didn't strip it (the original bug this replaces).
        content = "\ufeff" + GOOD_HEADER + GOOD_ROW
        books = parsers.parse_csv(content)
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["title"], "Book One")

    def test_no_bom_still_works(self):
        books = parsers.parse_csv((GOOD_HEADER + GOOD_ROW).encode("utf-8"))
        self.assertEqual(len(books), 1)

    def test_missing_or_invalid_numeric_fields_default_safely_not_500(self):
        csv_text = (
            "Title,Author,ISBN13,My Rating,Average Rating,Number of Pages\n"
            "Some Book,Some Author,,3,not-a-number,also-not-a-number\n"
        )
        books = parsers.parse_csv(csv_text.encode("utf-8"))
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["avg_rating"], 0.0)
        self.assertEqual(books[0]["num_pages"], 0)
        self.assertEqual(books[0]["my_rating"], 3)

    def test_row_missing_title_is_skipped_not_fatal(self):
        csv_text = (
            "Title,Author,My Rating\n"
            ",No Title Author,4\n"
            "Real Book,Real Author,5\n"
        )
        books = parsers.parse_csv(csv_text.encode("utf-8"))
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["title"], "Real Book")

    def test_unrated_rows_are_skipped_not_errors(self):
        csv_text = "Title,Author,My Rating\nUnrated Book,Some Author,0\nRated Book,Other Author,4\n"
        books = parsers.parse_csv(csv_text.encode("utf-8"))
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["title"], "Rated Book")

    def test_missing_title_column_raises_400_not_500(self):
        with self.assertRaises(HTTPException) as ctx:
            parsers.parse_csv(b"Foo,Bar\n1,2\n")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_empty_file_raises_400(self):
        with self.assertRaises(HTTPException) as ctx:
            parsers.parse_csv(b"")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_no_rated_books_raises_400_with_helpful_detail(self):
        csv_text = "Title,Author,My Rating\nSome Book,Some Author,0\n"
        with self.assertRaises(HTTPException) as ctx:
            parsers.parse_csv(csv_text.encode("utf-8"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_invalid_utf8_bytes_raise_400_not_500(self):
        with self.assertRaises(HTTPException) as ctx:
            parsers.parse_csv(b"\xff\xfe\x00Title,My Rating\n\x00\xffBad,5\n")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_year_read_parsed_from_date_read(self):
        csv_text = GOOD_HEADER + GOOD_ROW
        books = parsers.parse_csv(csv_text.encode("utf-8"))
        self.assertEqual(books[0]["year_read"], 2021)

    def test_rating_is_clamped_to_valid_range(self):
        csv_text = "Title,Author,My Rating\nSome Book,Some Author,99\n"
        books = parsers.parse_csv(csv_text.encode("utf-8"))
        self.assertEqual(books[0]["my_rating"], 5)


class ParseCsvWithWarningsTests(unittest.TestCase):
    """parse_csv_with_warnings surfaces malformed/skipped rows as warnings
    (item 9) while parse_csv itself stays list-returning for compatibility."""

    def test_no_warnings_when_all_rows_are_clean(self):
        books, warnings = parsers.parse_csv_with_warnings((GOOD_HEADER + GOOD_ROW).encode("utf-8"))
        self.assertEqual(len(books), 1)
        self.assertEqual(warnings, [])

    def test_malformed_row_produces_a_warning_but_still_returns_good_books(self):
        csv_text = (
            "Title,Author,My Rating\n"
            ",No Title Author,4\n"  # rated but missing title -> malformed, skipped
            "Real Book,Real Author,5\n"
        )
        books, warnings = parsers.parse_csv_with_warnings(csv_text.encode("utf-8"))
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["title"], "Real Book")
        self.assertEqual(len(warnings), 1)
        self.assertIn("1 row", warnings[0])

    def test_unrated_skipped_rows_do_not_generate_a_warning(self):
        # Unrated rows are an expected, non-error case (not-yet-read books),
        # distinct from malformed (rated-but-invalid) rows.
        csv_text = "Title,Author,My Rating\nUnrated Book,Some Author,0\nRated Book,Other Author,4\n"
        books, warnings = parsers.parse_csv_with_warnings(csv_text.encode("utf-8"))
        self.assertEqual(len(books), 1)
        self.assertEqual(warnings, [])

    def test_parse_csv_still_returns_a_plain_list_for_backward_compatibility(self):
        books = parsers.parse_csv((GOOD_HEADER + GOOD_ROW).encode("utf-8"))
        self.assertIsInstance(books, list)
        self.assertEqual(books[0]["title"], "Book One")


if __name__ == "__main__":
    unittest.main()
