from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from finbert.data import load_training_data


class FinbertDataTests(unittest.TestCase):
    def test_load_training_data_adds_canonical_label_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "training.csv"
            pd.DataFrame(
                [
                    {"text": "bad", "label_normalized": "negative", "split": "train"},
                    {"text": "ok", "label_normalized": "neutral", "split": "validation"},
                    {"text": "good", "label_normalized": "positive", "split": "test"},
                ]
            ).to_csv(path, index=False)

            df = load_training_data(csv_path=path)

            labels = dict(zip(df["label_normalized"], df["label_id"]))
            self.assertEqual(labels["negative"], 0)
            self.assertEqual(labels["neutral"], 1)
            self.assertEqual(labels["positive"], 2)


if __name__ == "__main__":
    unittest.main()
