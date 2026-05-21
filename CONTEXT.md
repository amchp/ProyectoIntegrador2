# Financial Sentiment Modeling

This context defines the shared language for training and deploying financial sentiment classifiers in this project.

## Language

**FinBERT sentiment model**:
A trained classifier that predicts financial sentiment labels for financial text.
_Avoid_: FinBERT program

**Feature snapshot**:
A dated, immutable training dataset export containing normalized text, sentiment labels, and split assignments.
_Avoid_: Dataset dump, current data

**Model artifact**:
A versioned bundle of files needed to load the trained FinBERT sentiment model for inference.
_Avoid_: Checkpoint when referring to deployable inference files

**Training run**:
One execution that consumes a feature snapshot and produces metrics plus a model artifact.
_Avoid_: Notebook run

**Deployment**:
A running HTTP service that loads one model artifact and returns sentiment predictions.
_Avoid_: Deploy when only files have been uploaded

## Example Dialogue

Dev: Which feature snapshot should this training run use?

Domain expert: Use the snapshot for 2026-05-20 so the model artifact can be traced back to the data used in the demo.

Dev: After training, should I deploy the checkpoint folder directly?

Domain expert: No. Create a model artifact that contains only the files needed for inference, then deploy that artifact as the FinBERT sentiment model service.
