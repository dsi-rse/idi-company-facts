# idi-company-facts

Extracts structured company facts (revenue, fiscal period, registered securities) from SEC 10-K and 20-F filings stored in the shared processor S3 bucket. The pipeline reads a manifest of scraped filing dates, downloads the iXBRL exhibit for each filing in the requested date window, and parses structured facts using `idi-ftm2j-shared`'s iXBRL parser. Output is a Parquet file at `{database_prefix}/company-facts/latest.parquet` in the shared bucket.

## Local quick start

```bash
# Install uv if needed: https://docs.astral.sh/uv/
uv sync --all-extras --all-groups

# Lint & format
uv run ruff check . --output-format=concise
uv run ruff format --check .

# Tests
uv run pytest
```

## Container usage

Build the production image:

```bash
docker build -f dockerfiles/Dockerfile.orchestrator -t idi-company-facts .
```

Run with explicit date range (requires AWS credentials in environment):

```bash
docker run --rm \
  -e AWS_REGION=us-east-2 \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  -e SEC_USER_AGENT="Name email@example.com" \
  idi-company-facts \
  --sec-bucket <bucket-name> \
  --output-file s3://<bucket>/<prefix>/latest.parquet \
  --failure-file s3://<bucket>/company-facts/failures/failures.json \
  --start-date 2024-01-01 --end-date 2024-03-31
```

Run in daily mode (looks back 7 days from latest scraped date in the manifest):

```bash
docker run --rm \
  -e AWS_REGION=us-east-2 \
  -e SEC_USER_AGENT="Name email@example.com" \
  idi-company-facts \
  --sec-bucket <bucket-name> \
  --output-file s3://<bucket>/database/company-facts/latest.parquet \
  --failure-file s3://<bucket>/company-facts/failures/failures.json \
  --daily
```

For local dev with Docker Compose (devcontainer), use the root `Dockerfile` and `docker-compose.yaml`.

## Orchestrator CLI reference

```
python -m idi_company_facts.orchestrator --help

required:
  --sec-bucket BUCKET       S3 bucket name for the SEC scraper data
  --output-file PATH        s3:// path for output parquet
  --failure-file PATH       s3:// path for failure registry
  --daily | --start-date YYYY-MM-DD

optional:
  --end-date YYYY-MM-DD     required with --start-date
  --look-back N             days to look back in --daily mode (default: 7)
  --failure-flush-every N   flush failures every N items (default: 50)
  --num-workers N           parallel fetch workers (default: 10)
```

## CI/CD

CI/CD uses reusable workflows from [`dsi-rse/idi-ftm2j-shared`](https://github.com/dsi-rse/idi-ftm2j-shared), pinned to an exact release tag in `.github/workflows/`:

- **`deploy.yml`** — triggered on push to `main` or `dev`; versions, builds/pushes the Docker image to GHCR and ECR, and deploys the Pulumi stack.
- **`checks.yml`** — triggered on PRs to `main` or `dev`; runs lint, tests, security scan (CodeQL), and a Pulumi preview.

The pin (`@v0.1.19`) is intentional — workflow behavior is frozen until the pin is bumped, making upgrades explicit and auditable.

## Secrets and variables

The following must be configured in GitHub before the shared workflows can run. See [onboarding §5](https://github.com/dsi-rse/idi-ftm2j-shared/blob/dev/docs/onboarding-a-processor.md#5-where-each-value-goes-routing-table) for the authoritative routing table.

| Value | Kind | Home |
|---|---|---|
| `AWS_ROLE_ARN_CHECKS` | Environment secret (`dev`, `prod`) | Per-repo GitHub environment |
| `AWS_ROLE_ARN_DEPLOY` | Environment secret (`dev`, `prod`) | Per-repo GitHub environment |
| `DEPLOY_KEY` | Repository secret | Per-repo GitHub secret |
| `PULUMI_CONFIG_PASSPHRASE` | Repository secret | Per-repo GitHub secret |
| `PULUMI_STATE_BUCKET` | Environment variable (`dev`, `prod`) | Per-repo GitHub environment |
| `PROD_INFRA_READY` | Repository variable | Per-repo GitHub variable |
| `AWS_REGION` | Organization variable | Org-level GitHub variable |

Non-secret pipeline configuration (cron, CPU/memory, worker count, etc.) is committed to `pulumi/Pulumi.dev.yaml` and `pulumi/Pulumi.prod.yaml`.

This processor has **no app-level secrets** (no API keys). The SEC User-Agent is a plain config value committed to the stack files.

## S3 layout

```
{shared-bucket}/
  sec/
    manifest.parquet          # written by idi-sec-scraper; read by this pipeline
    ...
  database/
    company-facts/
      latest.parquet          # output of this pipeline
  company-facts/
    failures/
      failures.json           # failure registry
```

## Pulumi infrastructure

The `pulumi/` directory provisions an AWS ECS Fargate task definition, ECR repository, CloudWatch log group, EventBridge schedule, and supporting IAM roles. Infrastructure is managed per-environment (`dev`, `prod`) via Pulumi stacks. The schedule is disabled by default (`schedule_enabled: "false"`) — enable it after the AWS stack is provisioned and verified.
