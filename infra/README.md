# AWS infrastructure (B-Roll Scout)

Deploys **DynamoDB**, **Lambda** (FastAPI + Mangum), **HTTP API** (API Gateway v2), **Secrets Manager** secret for API keys, and **optional custom domain** + **optional Route 53 alias** in **us-east-1** via **`template.yaml`** (AWS SAM).

## What I need from you (and what I do not)

- **You must have:** AWS credentials on the machine running `sam deploy` (CLI profile or environment variables) for the intended account.
- **I do not have:** Your AWS password, console-only access, or guaranteed linkage between an email and the account ID shown by `aws sts get-caller-identity`. Always verify the account ID in the console **Billing → Account** or IAM before deploying.

## What you need

1. **AWS account** with billing enabled (Lambda, DynamoDB, API Gateway, Secrets Manager).
2. **AWS CLI** configured (`aws configure` or SSO). Verify:

   ```bash
   aws sts get-caller-identity --region us-east-1
   ```

3. **AWS SAM CLI** (`sam`): `brew install aws-sam-cli`

4. **Build note (monorepo):** Next.js and Python share the `app/` directory. The **`Makefile`** copies only `**/*.py` into the Lambda artifact and installs **`requirements-lambda.txt`** with **Linux x86_64** wheels.

5. **Secrets Manager (required in cloud):** The stack creates a secret named like `broll-scout/<stack>/app-keys`. Lambda reads it using **`SECRETS_ARN`** (set automatically). After deploy, set JSON:

   ```json
   {
     "OPENAI_API_KEY": "sk-...",
     "GEMINI_API_KEY": "",
     "YOUTUBE_API_KEY": "",
     "GOOGLE_SEARCH_API_KEY": "",
     "GOOGLE_SEARCH_CX": "",
     "API_KEY": ""
   }
   ```

   Example CLI (replace `SECRET_ARN` with stack output **AppSecretsArn**):

   ```bash
   aws secretsmanager put-secret-value --region us-east-1 \
     --secret-id SECRET_ARN \
     --secret-string file://keys.json
   ```

   **Local dev:** Leave `SECRETS_ARN` unset; use `.env` with the same variable names as today (`OPENAI_API_KEY`, etc.). `app/config.py` merges Secrets Manager only when `SECRETS_ARN` is present.

**IAM:** Prefer an **IAM user or role** with least privilege, **not** the account root user, for day-to-day CLI access.

## Custom domain (`broll.jayasim.com`)

1. **ACM (us-east-1):** Request a **public** certificate for `broll.jayasim.com` (DNS validation in Route 53 or your DNS provider). Wait until status is **Issued**.
2. **Deploy / update stack** with parameters:

   - `AcmCertificateArn` = that certificate’s ARN  
   - `CustomDomainName` = `broll.jayasim.com` (default)  
   - `HostedZoneId` = Route 53 hosted zone ID for `jayasim.com` **if** you want this stack to create the **A alias** automatically; otherwise leave empty and create DNS yourself.

3. **DNS without Route 53 in stack:** Use output **ApiGatewayRegionalTarget** (API Gateway regional domain name) as a **CNAME** target for `broll.jayasim.com`, or an **ALIAS** at your DNS provider if supported.

4. **Frontend / clients:** Set `BACKEND_URL=https://broll.jayasim.com` (or the default **HttpApiUrl** if you skip custom domain).

## Long-running jobs: do you need more than “Lambda + HTTP API”?

**Yes, for reliability you should treat the pipeline as asynchronous work, not as “fire-and-forget inside the same request.”**

| Constraint | Why it matters |
|------------|----------------|
| **API Gateway → Lambda** | For HTTP APIs, the integration timeout is **up to 30 seconds** for the client to receive a response from the integration. Your `POST /api/v1/jobs` returns a small JSON body quickly, so the **HTTP response** path is fine. |
| **FastAPI `BackgroundTasks`** | Work runs **after** the response is sent, but on Lambda there is **no durable guarantee** the execution environment stays alive until the job finishes; scaling, freezes, and failures can drop work. |
| **Job duration** | Your pipeline is designed for **multi-minute** runs (many segments × APIs). That fits **Lambda’s 15-minute limit** only if the invocation **actually runs to completion** — which is uncertain with background tasks on Lambda. |

**Recommendation:** Keep **`POST /api/v1/jobs`** as “enqueue + return `job_id`” and run **`run_pipeline`** in a **separate invocation**: e.g. **SQS** → consumer Lambda (or **Step Functions** state machine, or **Fargate** worker). Same code, clearer operations and retries.

The current template still allows **timeout 900s** and **2048 MB** for when you move the worker to its own handler or queue.

## Deploy (first time)

From the **repository root**:

```bash
sam build
sam deploy --guided
```

Use parameters for custom domain when ready (`AcmCertificateArn`, `HostedZoneId` optional).

**Before** updating an older stack that put API keys in Lambda env: **populate the Secrets Manager secret first**, then deploy so the function does not lose access to keys.

## Deploy (repeat)

```bash
sam build && sam deploy
```

## DynamoDB only (no Lambda)

The SAM stack creates the same tables as `scripts/create_tables.py` with prefix `broll_` by default.

## Cost notes

- DynamoDB on-demand, Lambda, HTTP API, Secrets Manager API calls (GetSecretValue per cold start / call pattern).

See [AWS Pricing](https://aws.amazon.com/pricing/).
