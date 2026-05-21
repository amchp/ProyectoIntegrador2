# Infra

This folder contains Python scripts to create the AWS resources for the financial sentiment pipeline.

## Run order

Run commands from the `infra/` folder.

For a fresh environment, use this order:

```bash
# 1. Load AWS and project settings.
set -a
source .env
set +a

# 2. Install local infra script dependencies.
python -m pip install -r requirements.txt

# 3. Create base AWS infrastructure.
python create_vpc.py
python create_security_groups.py
python create_raw_bucket.py
python create_features_bucket.py
python create_rds_postgres.py
python create_glue_connection.py
python create_finbert_kinesis.py

# 4. Deploy ETL orchestration.
python deploy_glue_jobs.py
python deploy_step_function.py

# 5. Run the data pipeline to materialize a feature snapshot.
python start_pipeline.py
# Or pin the snapshot date:
python start_pipeline.py --snapshot-date 2026-05-20
```

For normal reruns after infrastructure already exists, use:

```bash
set -a
source .env
set +a

python deploy_glue_jobs.py
python deploy_step_function.py
python start_pipeline.py
```

## 1. Load environment variables

From this `infra/` folder:

```bash
set -a
source .env
set +a
```

The `.env` file should contain:

```bash
AWS_PROFILE=your-aws-profile
AWS_REGION=us-east-1
RDS_MASTER_PASSWORD=your-secure-password
GLUE_SERVICE_ROLE_ARN=arn:aws:iam::<account-id>:role/service-role/AWSGlueServiceRoleFinancialSentiment
STATE_MACHINE_ROLE_ARN=arn:aws:iam::<account-id>:role/service-role/FinancialSentimentStepFunctionsRole
```

If you are not using `AWS_PROFILE`, fill in these instead:

```bash
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=
```

## 2. Install dependencies

From this `infra/` folder:

```bash
python -m pip install -r requirements.txt
```

## 3. Create the VPC

```bash
python create_vpc.py
```

This creates the VPC, public subnets, private subnets, route tables, and an S3 Gateway endpoint for the private subnet route table. S3 is not placed inside the VPC; the endpoint gives private subnets a private AWS route to S3.
The script is idempotent: rerunning it reuses existing resources and updates route table associations or endpoint routes when needed.

Copy the printed values:

- `VPC ready: vpc-...`
- `Public subnets for EC2 public IPs: subnet-..., subnet-...`
- `Private subnets for RDS: subnet-..., subnet-...`

Then update these values in `.env`:

- `VPC_ID=vpc-...`
- `PUBLIC_SUBNET_IDS=subnet-...,subnet-...`
- `PRIVATE_SUBNET_IDS=subnet-...,subnet-...`

## 4. Create security groups

```bash
python create_security_groups.py
```

This creates:

- `proyecto-integrador-ec2-sg`
- `proyecto-postgres-sg`
- `proyecto-finbert-lambda-sg`

It also allows `proyecto-finbert-lambda-sg` to call the FinBERT API on `proyecto-integrador-ec2-sg` through port `8000/tcp`.

The script uses these CIDRs from `.env` when present:

```bash
SSH_ALLOWED_CIDR=your-public-ip/32
API_ALLOWED_CIDR=your-public-ip/32
```

If either value is missing, the script detects the current public IP of the machine running the command and uses `<current-public-ip>/32`.
The FinBERT API port is `8000/tcp` and should be restricted to your public IP for the demo.
The PostgreSQL security group also gets a self-referencing all-traffic inbound rule. AWS Glue requires this for JDBC connections that run inside a VPC.

## 5. Create S3 buckets

```bash
python create_raw_bucket.py
python create_features_bucket.py
```

The hardcoded bucket names are:

- `proyecto-integrador-2`
- `proyecto-integrador-2-features-amce`

Run `python create_vpc.py` again if an existing VPC needs the private route table or S3 endpoint updated.

## 6. Create RDS PostgreSQL

Make sure `RDS_MASTER_PASSWORD` is loaded from `.env`, then run:

```bash
python create_rds_postgres.py
```

This creates the database:

- Instance: `proyecto-postgres`
- Database: `proyectodb`
- Username: `postgres`
- Port: `5432`

The script waits until the RDS instance is available and prints the endpoint.

## 7. Create the Glue connection

Before deploying Glue jobs, create the AWS Glue connection:

```bash
python create_glue_connection.py
```

This creates or updates:

```text
Proyecto Financial Sentiment RDS connection
```

The script points it to the RDS PostgreSQL instance and uses the first subnet from `PRIVATE_SUBNET_IDS` plus the `proyecto-postgres-sg` security group.

## 8. Deploy Glue jobs

Before running this, make sure `GLUE_SERVICE_ROLE_ARN` in `.env` is a real IAM role ARN in your AWS account. The identity running this command also needs `iam:PassRole` permission for that role.

```bash
python deploy_glue_jobs.py
```

This deploys:

- `glue_financial_sentiment_raw`
- `glue_financial_sentiment_curated`
- `glue_financial_sentiment_features`

The raw job is configured with `MaxConcurrentRuns = 1` because the Step Function runs raw dataset jobs sequentially.
Glue job Python dependencies are read from `glue/requirements_raw.txt`, `glue/requirements_curated.txt`, and `glue/requirements_features.txt`.
Curated and features run in private VPC subnets, so their requirement files are intentionally empty unless you add NAT or a private package index. S3 Gateway endpoints do not provide internet access to PyPI.
The Glue common bundle includes `glue/common/pandas_compat.py` so pandas 2.x works with Glue/Spark code paths that still expect `DataFrame.iteritems`.

## 9. Deploy the Step Functions state machine

Before running this, make sure `STATE_MACHINE_ROLE_ARN` in `.env` is a real IAM role ARN in your AWS account. The identity running this command also needs `iam:PassRole` permission for that role.

```bash
python deploy_step_function.py
```

This creates or updates:

```text
financial-sentiment-raw-curated-features
```

The state machine flow is:

```text
Raw datasets -> Curated Postgres -> Daily Feature Snapshot
```

The features job writes only the requested daily snapshot partition:

```text
s3://proyecto-integrador-2-features-amce/features/financial_sentiment/model_features/snapshot_date=YYYY-MM-DD/
s3://proyecto-integrador-2-features-amce/features/financial_sentiment/reports/snapshot_date=YYYY-MM-DD/
```

Rerunning the features job for the same `snapshot_date` deletes and replaces only that partition.

## 10. Start the snapshot pipeline

```bash
python start_pipeline.py
python start_pipeline.py --snapshot-date 2026-05-20
```

The start script sends explicit Step Functions input:

```json
{
  "snapshot_date": "YYYY-MM-DD",
  "run_id": "YYYYMMDD-HHMMSS"
}
```

The Step Function stops after the snapshot parquet partition is written.

## 11. Deploy FinBERT on EC2

This demo path deploys the FinBERT sentiment model as a FastAPI service on one EC2 instance. The lab-friendly default is `t3.micro`; set `FINBERT_INSTANCE_TYPE=g4dn.xlarge` only when GPU EC2 is allowed.

Add these values to `.env`:

```bash
EC2_KEY_NAME=your-aws-key-pair-name
EC2_KEY_PATH=/local/path/to/your-key.pem
SSH_ALLOWED_CIDR=your-public-ip/32
API_ALLOWED_CIDR=your-public-ip/32
PUBLIC_SUBNET_IDS=subnet-...,subnet-...

FINBERT_INSTANCE_TYPE=t3.micro

FINBERT_INSTANCE_PROFILE_NAME=LabRole
```

`PUBLIC_SUBNET_IDS` is printed by `python create_vpc.py`. If it is missing, `create_finbert_ec2.py` tries to discover a public subnet in `VPC_ID` automatically.
`SSH_ALLOWED_CIDR` and `API_ALLOWED_CIDR` are your client IP ranges, not the EC2 Elastic IP. The Elastic IP can be allocated before EC2 exists; `create_finbert_ec2.py` associates it after the instance is running.

Allocate the stable Elastic IP, create or update security groups, then launch or start EC2:

```bash
cd infra
set -a
source .env
set +a

python create_finbert_elastic_ip.py
python create_security_groups.py
python create_finbert_ec2.py
```

Deploy the API from your local machine. By default, this uploads `artifacts/finbert/checkpoint-9700` to S3 under `models/finbert/manual/run_id=.../`, has EC2 download that S3 artifact with `aws s3 sync`, installs dependencies, starts `finbert-api.service`, and verifies `/health`:

```bash
cd infra
set -a
source .env
set +a

python upload_finbert_artifact.py
python deploy_finbert_service.py --artifact-uri <artifact_uri printed by upload_finbert_artifact.py>
```

`deploy_finbert_service.py` bootstraps the EC2 service environment with `uv` and Python `3.13.13`, matching the FinBERT notebook kernel. To force another runtime:

```bash
python deploy_finbert_service.py \
  --python-version 3.11 \
  --artifact-uri <artifact_uri printed by upload_finbert_artifact.py>
```

The S3 download on EC2 uses the instance profile credentials automatically; no AWS keys are copied to the machine.

To deploy a different local trained checkpoint:

```bash
python upload_finbert_artifact.py \
  --model-dir ../artifacts/finbert_sentiment_only_v1/checkpoint-4208

python deploy_finbert_service.py --artifact-uri <artifact_uri printed by upload_finbert_artifact.py>
```

To deploy an artifact that already exists in S3, pass `--artifact-uri`:

```bash
cd infra
python deploy_finbert_service.py \
  --artifact-uri s3://proyecto-integrador-2-features-amce/models/finbert/snapshot_date=2026-05-20/run_id=<run_id>/
```

Test the service:

```bash
curl http://<ec2-public-ip>:8000/health

curl -X POST http://<ec2-public-ip>:8000/predict \
  -H 'Content-Type: application/json' \
  -d '{"text":"Apple shares rose after earnings beat expectations."}'
```

If you associated an Elastic IP, use that IP for the API URL:

```bash
curl http://<elastic-ip>:8000/health
```

Stop the GPU instance after the demo:

```bash
python stop_finbert_ec2.py
```

## 12. Deploy Kinesis-triggered FinBERT inference

This path adds asynchronous inference without replacing the HTTP API:

```text
local machine -> Kinesis -> Lambda -> EC2 HTTP API -> S3
```

Create the Kinesis stream and make sure the Lambda security group rule exists:

```bash
cd infra
set -a
source .env
set +a

python create_finbert_kinesis.py
python create_security_groups.py
```

Deploy the Lambda consumer after the FinBERT EC2 API is running:

```bash
python deploy_finbert_lambda_consumer.py
```

The deploy script uses the running EC2 instance tagged `Name=proyecto-finbert-ec2`, stores its private API URL in Lambda configuration, attaches `proyecto-finbert-lambda-sg`, and creates a Kinesis event source mapping with batch size `1` and starting position `LATEST`.

Send one request from your local machine:

```bash
python send_finbert_kinesis_request.py \
  --text "Apple shares rose after earnings beat expectations."
```

The script prints the `request_id`, Kinesis sequence number, and expected S3 result URI. Results are written under:

```text
s3://proyecto-integrador-2-features-amce/inference/finbert/results/date=YYYY-MM-DD/<request_id>.json
```

The Lambda writes either `status: "success"` with the model prediction or `status: "error"` with a validation/API error. Expected request or API failures are written to S3 instead of being retried forever.

## IAM notes

The identity running `deploy_step_function.py` needs `iam:PassRole` for `STATE_MACHINE_ROLE_ARN`.
The Step Functions role itself needs permission to start and monitor the three Glue jobs.
The identity running `create_finbert_ec2.py` needs EC2 permissions and, unless `FINBERT_INSTANCE_PROFILE_NAME` points to an existing profile, IAM permissions to create/update `proyecto-finbert-ec2-role` and `proyecto-finbert-ec2-profile`.
If the AWS account denies `iam:CreateRole`, use a pre-created EC2 instance profile instead. The script automatically tries `LabRole` and `LabInstanceProfile`; you can also set one explicitly:

```bash
FINBERT_INSTANCE_PROFILE_NAME=LabRole
```

The profile must allow the EC2 instance to read:

```text
s3://proyecto-integrador-2-features-amce/features/financial_sentiment/model_features/*
```

and read/write:

```text
s3://proyecto-integrador-2-features-amce/models/finbert/*
```

## Troubleshooting

If an EC2 command fails with an explicit deny like this:

```text
UnauthorizedOperation ... is not authorized to perform: ec2:DescribeVpcs with an explicit deny in an identity-based policy: ...:policy/voc-cancel-cred
```

The active AWS credentials are blocked by the lab/account policy. This is not caused by the repo scripts and cannot be fixed by adding permissions in code. In AWS Academy/Vocareum-style labs, restart or renew the lab session, download/copy fresh credentials, update `infra/.env`, then reload them:

```bash
cd infra
set -a
source .env
set +a
aws sts get-caller-identity
```

After the caller identity shows the fresh session, rerun the infra command.
