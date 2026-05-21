from __future__ import annotations

import unittest

try:
    from fastapi import HTTPException

    from finbert.service import PredictRequest, _request_texts
except Exception:  # pragma: no cover - dependency availability differs outside EC2.
    HTTPException = None
    PredictRequest = None
    _request_texts = None


@unittest.skipIf(PredictRequest is None, "FastAPI service dependencies are not installed")
class FinbertServiceValidationTests(unittest.TestCase):
    def test_accepts_single_text(self) -> None:
        self.assertEqual(_request_texts(PredictRequest(text=" hello ")), ["hello"])

    def test_accepts_batch_texts(self) -> None:
        self.assertEqual(_request_texts(PredictRequest(texts=[" a ", "b"])), ["a", "b"])

    def test_rejects_missing_text(self) -> None:
        with self.assertRaises(HTTPException):
            _request_texts(PredictRequest())

    def test_rejects_empty_text_in_batch(self) -> None:
        with self.assertRaises(HTTPException):
            _request_texts(PredictRequest(texts=["ok", " "]))


if __name__ == "__main__":
    unittest.main()
