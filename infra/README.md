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
- `Private subnets for RDS: subnet-..., subnet-...`

Then update these values in `.env`:

- `VPC_ID=vpc-...`
- `PRIVATE_SUBNET_IDS=subnet-...,subnet-...`

## 4. Create security groups

```bash
python create_security_groups.py
```

This creates:

- `proyecto-integrador-ec2-sg`
- `proyecto-postgres-sg`

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

## IAM notes

The identity running `deploy_step_function.py` needs `iam:PassRole` for `STATE_MACHINE_ROLE_ARN`.
The Step Functions role itself needs permission to start and monitor the three Glue jobs.
