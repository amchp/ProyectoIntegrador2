from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoModelForSequenceClassification, AutoTokenizer


CANONICAL_LABELS = ["negative", "neutral", "positive"]
DEFAULT_MODEL_LABEL_ORDER = ["positive", "negative", "neutral"]


class PredictRequest(BaseModel):
    text: Optional[str] = None
    texts: Optional[List[str]] = None


class FinbertRuntime:
    def __init__(self) -> None:
        self.artifact_uri = os.getenv("FINBERT_ARTIFACT_URI", "")
        self.model_name = os.getenv("FINBERT_MODEL_NAME", "ProsusAI/finbert")
        self.model_dir = Path(os.getenv("FINBERT_MODEL_DIR", "/opt/finbert/model"))
        self.max_batch_size = int(os.getenv("FINBERT_MAX_BATCH_SIZE", "32"))
        self.max_length = int(os.getenv("FINBERT_MAX_LENGTH", "128"))
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer: Any | None = None
        self.model: Any | None = None

    def load(self) -> None:
        model_source = self.model_dir if (self.model_dir / "config.json").exists() else self.model_name
        self.tokenizer = AutoTokenizer.from_pretrained(model_source)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_source).to(self.device)
        self.model.eval()

    @property
    def loaded(self) -> bool:
        return self.tokenizer is not None and self.model is not None

    def _model_label(self, index: int) -> str:
        if self.model is None:
            return DEFAULT_MODEL_LABEL_ORDER[index]
        raw = str(self.model.config.id2label.get(index, DEFAULT_MODEL_LABEL_ORDER[index]))
        if raw.startswith("LABEL_"):
            return DEFAULT_MODEL_LABEL_ORDER[index]
        return raw.lower()

    def predict(self, texts: List[str]) -> List[dict[str, Any]]:
        if not self.loaded:
            raise HTTPException(status_code=503, detail="Model is not loaded.")
        if len(texts) > self.max_batch_size:
            raise HTTPException(status_code=400, detail=f"Batch size cannot exceed {self.max_batch_size}.")

        assert self.tokenizer is not None
        assert self.model is not None
        batch = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        batch = {key: value.to(self.device) for key, value in batch.items()}

        with torch.inference_mode():
            probabilities = torch.softmax(self.model(**batch).logits, dim=-1).cpu()

        predictions = []
        for text, row in zip(texts, probabilities):
            scores_by_label = {
                self._model_label(index): float(score)
                for index, score in enumerate(row.tolist())
            }
            canonical_scores = {label: float(scores_by_label.get(label, 0.0)) for label in CANONICAL_LABELS}
            label = max(canonical_scores, key=canonical_scores.get)
            predictions.append(
                {
                    "text": text,
                    "label": label,
                    "score": canonical_scores[label],
                    "probabilities": canonical_scores,
                }
            )
        return predictions


runtime = FinbertRuntime()
app = FastAPI(title="FinBERT Sentiment API")


@app.on_event("startup")
def load_model() -> None:
    runtime.load()


@app.get("/health")
def health():
        return {
            "status": "ok" if runtime.loaded else "loading",
            "model_loaded": runtime.loaded,
            "device": str(runtime.device),
            "artifact_uri": runtime.artifact_uri,
            "model_name": runtime.model_name,
            "labels": CANONICAL_LABELS,
        }


def _request_texts(request: PredictRequest) -> List[str]:
    if request.text is not None and request.text.strip():
        if request.texts is not None:
            raise HTTPException(status_code=400, detail="Provide either text or texts, not both.")
        return [request.text.strip()]
    if request.texts is None:
        raise HTTPException(status_code=400, detail="Provide text or texts.")
    texts = [text.strip() for text in request.texts if text and text.strip()]
    if len(texts) != len(request.texts):
        raise HTTPException(status_code=400, detail="texts cannot contain empty strings.")
    if not texts:
        raise HTTPException(status_code=400, detail="texts cannot be empty.")
    return texts


@app.post("/predict")
def predict(request: PredictRequest):
    return {"predictions": runtime.predict(_request_texts(request))}
