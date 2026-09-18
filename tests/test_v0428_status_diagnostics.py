import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class StatusDiagnosticsV0428Tests(unittest.TestCase):
    def test_diagnostic_display_uses_the_actual_algorithm_bucket_and_exclusion_names(self):
        from helper import recommend

        diagnostic_display = getattr(recommend, "diagnostic_display", None)
        self.assertTrue(callable(diagnostic_display))

        result = diagnostic_display(
            {
                "candidate_count": 2681,
                "selected": 50,
                "bucket_counts": {
                    "稳定喜好": 39,
                    "近期口味": 3,
                    "久未重听": 0,
                    "曲库探索": 8,
                },
                "excluded_never_recommend": 4,
                "excluded_recent_plays": 16,
                "excluded_by_fatigue": 2,
                "excluded_recent_daily": 7,
            }
        )

        self.assertEqual({"candidate": 2681, "selected": 50}, result["summary"])
        self.assertEqual(
            {"stable": 39, "recent": 3, "rediscovery": 0, "exploration": 8},
            result["buckets"],
        )
        self.assertEqual(
            {"manual": 4, "recent_plays": 16, "fatigue": 2, "recent_daily": 7},
            result["exclusions"],
        )


if __name__ == "__main__":
    unittest.main()
