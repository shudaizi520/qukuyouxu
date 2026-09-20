import unittest


def row(title, artists, status="missing"):
    return {"title": title, "artists": artists, "status": status}


class ExternalExportV130Tests(unittest.TestCase):
    def test_text_contains_only_unique_current_missing_tracks(self):
        from helper.external_export import format_missing_text

        body = format_missing_text([
            row("歌一", ["甲"]), row("歌二", ["乙"], "matched"), row("歌一", ["甲"]),
        ])
        self.assertEqual("歌一 - 甲\n", body)

    def test_text_removes_line_breaks_and_keeps_all_artists(self):
        from helper.external_export import format_missing_text

        self.assertEqual("歌 一 - 甲 / 乙\n", format_missing_text([row("歌\r\n一", ["甲\n", "乙"])]))

    def test_csv_has_bom_headers_and_escapes_formula_cells(self):
        from helper.external_export import format_missing_csv

        data = format_missing_csv([row("=HYPERLINK(\"x\")", ["+歌手"]), row("正常", ["乙"], "review")])
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        text = data.decode("utf-8-sig")
        self.assertIn("歌名,歌手", text)
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("'+歌手", text)
        self.assertNotIn("正常", text)

    def test_export_rejects_invalid_rows_and_bounds_output(self):
        from helper.external_export import format_missing_text

        with self.assertRaises(ValueError):
            format_missing_text([{"title": "歌", "artists": "不是列表", "status": "missing"}])
        with self.assertRaises(ValueError):
            format_missing_text([row("歌", ["手"]) for _ in range(10001)])

    def test_download_filename_is_sanitized(self):
        from helper.external_export import missing_download_name

        self.assertEqual("百万 收藏-缺失歌曲.csv", missing_download_name("../百万\r收藏:*?", "csv"))


if __name__ == "__main__":
    unittest.main()
