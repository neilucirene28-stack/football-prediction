"""Raw source intake never backdates an old file into a pre-match feature."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from beidan_bd1.capture import capture_payload, verify_receipt, receipt_source
from beidan_bd1.snapshot import _datetime


class CaptureTests(unittest.TestCase):
    def test_real_ingest_time_and_immutable_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / "old-export.json"
            payload.write_text(json.dumps({"home": "甲", "away": "乙"}), encoding="utf-8")
            os.utime(payload, (1000000000, 1000000000))
            start = datetime.now(timezone.utc)
            receipt = capture_payload(payload_file=payload, root=Path(tmp) / "archive",
                                      period="26103", match_id="26103-1",
                                      source="roster", kind="fixture")
            self.assertGreaterEqual(_datetime(receipt["available_at"], "available_at"), start)
            self.assertEqual(receipt["model_eligibility"], "unassessed")
            self.assertTrue(verify_receipt(receipt, Path(tmp) / "archive"))
            self.assertEqual(receipt_source(receipt, Path(tmp) / "archive")["name"], "roster")
            archived = Path(tmp) / "archive" / receipt["payload_path"]
            archived.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "摘要"):
                verify_receipt(receipt, Path(tmp) / "archive")


if __name__ == "__main__":
    unittest.main()
