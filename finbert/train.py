from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import torch

from finbert.artifacts import (
    build_feature_snapshot_uri,
    build_model_artifact_uri,
    prepare_deployable_artifact,
    upload_directory_to_s3,
)
from finbert.data import load_training_data
from utils.text_utils import ID_TO_LABEL, set_seed
from utils.transformer_utils import build_transformer_trainer, evaluate_transformer_checkpoint


DEFAULT_FEATURES_BUCKET = "proyecto-integrador-2-features-amce"
DEFAULT_FEATURES_PREFIX = "features/financial_sentiment"
DEFAULT_ARTIFACT_BUCKET = "proyecto-integrador-2-features-amce"
DEFAULT_ARTIFACT_PREFIX = "models/finbert"
DEFAULT_MODEL_NAME = "ProsusAI/finbert"
DEFAULT_MODEL_LABEL_ORDER = ["positive", "negative", "neutral"]


def _numeric_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in metrics.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = float(value)
    return result


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _limit_split_rows(df, *, split_name: str, max_rows: int, seed: int):
    if max_rows <= 0:
        return df
    split_df = df[df["split"] == split_name]
    other_df = df[df["split"] != split_name]
    if len(split_df) <= max_rows:
        return df
    limited = split_df.sample(n=max_rows, random_state=seed)
    return pd.concat([other_df, limited], ignore_index=True).sort_values(["split"], kind="stable").reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and package the FinBERT sentiment model.")
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--features-bucket", default=DEFAULT_FEATURES_BUCKET)
    parser.add_argument("--features-prefix", default=DEFAULT_FEATURES_PREFIX)
    parser.add_argument("--artifact-bucket", default=DEFAULT_ARTIFACT_BUCKET)
    parser.add_argument("--artifact-prefix", default=DEFAULT_ARTIFACT_PREFIX)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--train-batch-size", type=int, default=16)
    parser.add_argument("--eval-batch-size", type=int, default=32)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--local-csv", default="")
    parser.add_argument("--work-dir", default="artifacts/finbert_runs")
    parser.add_argument("--max-train-rows", type=int, default=0)
    parser.add_argument("--max-validation-rows", type=int, default=0)
    parser.add_argument("--max-test-rows", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    set_seed(args.seed)
    run_id = _run_id()

    feature_snapshot_uri = build_feature_snapshot_uri(
        bucket=args.features_bucket,
        features_prefix=args.features_prefix,
        snapshot_date=args.snapshot_date,
    )
    artifact_uri = build_model_artifact_uri(
        bucket=args.artifact_bucket,
        artifact_prefix=args.artifact_prefix,
        snapshot_date=args.snapshot_date,
        run_id=run_id,
    )
    run_dir = Path(args.work_dir) / args.snapshot_date / run_id
    checkpoint_output_dir = run_dir / "trainer"
    package_dir = run_dir / "deployable"

    df = load_training_data(
        csv_path=args.local_csv or None,
        s3_uri=None if args.local_csv else feature_snapshot_uri,
    )
    df = _limit_split_rows(df, split_name="train", max_rows=args.max_train_rows, seed=args.seed)
    df = _limit_split_rows(df, split_name="validation", max_rows=args.max_validation_rows, seed=args.seed)
    df = _limit_split_rows(df, split_name="test", max_rows=args.max_test_rows, seed=args.seed)
    trainer, datasets, _tokenizer = build_transformer_trainer(
        df,
        model_name=args.model_name,
        model_label_order=DEFAULT_MODEL_LABEL_ORDER,
        output_dir=str(checkpoint_output_dir),
        max_length=args.max_length,
        epochs=args.epochs,
        train_batch_size=args.train_batch_size,
        eval_batch_size=args.eval_batch_size,
        learning_rate=args.learning_rate,
        seed=args.seed,
    )

    train_result = trainer.train()
    test_metrics = trainer.evaluate(datasets["test"])
    best_checkpoint = Path(trainer.state.best_model_checkpoint or checkpoint_output_dir)
    final_results = evaluate_transformer_checkpoint(
        str(best_checkpoint),
        DEFAULT_MODEL_LABEL_ORDER,
        df,
        split="test",
        max_length=args.max_length,
        batch_size=args.eval_batch_size,
    )

    metrics = {
        "train": _numeric_metrics(train_result.metrics),
        "test": _numeric_metrics(test_metrics),
        "final_test": _numeric_metrics(final_results),
    }
    metadata = {
        "run_id": run_id,
        "snapshot_date": args.snapshot_date,
        "feature_snapshot_uri": feature_snapshot_uri,
        "artifact_uri": artifact_uri,
        "base_model_name": args.model_name,
        "best_checkpoint": str(best_checkpoint),
        "model_label_order": DEFAULT_MODEL_LABEL_ORDER,
        "canonical_labels": {str(key): value for key, value in ID_TO_LABEL.items()},
        "epochs": args.epochs,
        "train_batch_size": args.train_batch_size,
        "eval_batch_size": args.eval_batch_size,
        "max_length": args.max_length,
        "learning_rate": args.learning_rate,
        "seed": args.seed,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
    }

    prepare_deployable_artifact(
        checkpoint_dir=best_checkpoint,
        output_dir=package_dir,
        metrics=metrics,
        metadata=metadata,
    )
    upload_directory_to_s3(package_dir, artifact_uri)

    final_test = metrics["final_test"]
    print(f"artifact_uri={artifact_uri}")
    print(f"test_macro_f1={final_test.get('macro_f1', final_test.get('eval_macro_f1', ''))}")
    print(f"test_accuracy={final_test.get('accuracy', final_test.get('eval_accuracy', ''))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
