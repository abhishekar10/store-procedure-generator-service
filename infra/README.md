# Infrastructure (AWS, Terraform)

Two Terraform root stacks in `ap-south-1` (Mumbai), built from small modules:

| Stack | What | Lifetime |
|---|---|---|
| [`demo/`](demo/) | The service: network, ECR, ECS Fargate behind an ALB, and the UI on S3 + CloudFront | Up for a demo, destroyed after (`make up` / `make down`) |
| [`account/`](account/) | Guard rails: the monthly budget and its kill switch | Long-lived; `make down` never touches it |

| Module | |
|---|---|
| [`modules/network`](modules/network/) | VPC, two public subnets in two AZs, internet gateway. No NAT gateway |
| [`modules/ecr`](modules/ecr/) | Image repository: immutable tags, scan on push, keeps the newest 5 |
| [`modules/service`](modules/service/) | ECS cluster, task definition, service; ALB, target group (health check `/ready`), security groups, execution role, log group |
| [`modules/frontend`](modules/frontend/) | Private S3 bucket and one CloudFront distribution for UI and API |
| [`modules/kill_switch`](modules/kill_switch/) | SNS topics and a Lambda that stops the hourly costs when the budget's top threshold is crossed |

State is **local** (`terraform.tfstate` in each stack directory, gitignored):
one owner, short-lived stacks. A shared S3 backend would be the next step for
more than one operator. Provider versions are pinned by the committed
`.terraform.lock.hcl` files.

## How requests flow

```
browser --HTTPS--> CloudFront --+-- /            --> S3 (UI, private, via Origin Access Control)
                                +-- /v1/*, /api/* --HTTP--> ALB --> ECS task :8000
```

There is no domain, so the ALB has no certificate and serves HTTP only.
CloudFront terminates HTTPS on its `*.cloudfront.net` name and serves both
the UI and the API from it: the browser sees one origin, so there is no CORS
and no HTTPS page calling an HTTP API (which browsers block). The leg from
CloudFront to the ALB is **plain HTTP** over AWS's network -- the honest
cost of not having a domain; with one, the ALB would get an ACM certificate
and CloudFront would use HTTPS to it.

Only CloudFront can reach the ALB: its security group admits only AWS's
managed CloudFront origin-facing prefix list, and the listener forwards only
requests carrying a random header (`X-Origin-Verify`) that this stack's
distribution adds -- anything else gets a 403. Tasks accept traffic only
from the ALB and send only HTTPS out (ECR, Secrets Manager, CloudWatch Logs,
Gemini). They have public IPs because there is no NAT gateway, but no
inbound path except through the ALB.

Timeouts along the way: CloudFront's origin read timeout is set to 60s, the
most allowed without a quota increase. The streaming endpoint, which the UI
uses, sends a status event immediately and a heartbeat every 15s, so it is
unaffected. **Non-streaming `/v1/generate` must answer within 60s** through
CloudFront; at the default thinking level it takes about 15s. The ALB's idle
timeout is 300s and the app's generation timeout is 200s.

## Usage

Prerequisites: the AWS CLI with an SSO profile `sp-generator`
(`aws sso login --profile sp-generator` when the session expires), Terraform
>= 1.10, Docker, and `GEMINI_API_KEY` in the repo-root `.env`.

```bash
make plan     # what `make up` would create -- read-only
make up       # create everything and deploy (COSTS MONEY), prints the URL
make status   # up (with the URL) or down
make down     # destroy everything make up created
```

`make up` (`scripts/up.sh`) asks for approval once, on a plan of the whole
stack, then:

1. creates the stack with the service at **zero tasks**;
2. writes `GEMINI_API_KEY` from `.env` into Secrets Manager with
   `put-secret-value` -- the key never appears in Terraform state or plans;
3. builds the API image (the corpus is embedded at build time, with the key
   passed as a BuildKit secret) and pushes it to ECR tagged with the git commit;
4. builds the UI with an empty `VITE_API_BASE_URL`, so it calls `/v1/...` on
   its own origin, uploads it to S3 and invalidates CloudFront;
5. scales the service to one task and waits until it is healthy.

A first `make up` is expected to take 10-15 minutes, mostly CloudFront (not yet run). `make down`
deletes everything in the demo stack, including the ECR images, the UI bucket
and the secret (no recovery window, so the next `make up` can reuse the name).

## Cost

Hourly charges while the demo stack is up, from the AWS Price List API for
`ap-south-1` (checked 2026-11-14):

| | Rate | Per hour |
|---|---|---|
| Fargate, 0.25 vCPU + 0.5 GB | $0.04256/vCPU-h, $0.004655/GB-h | $0.0130 |
| ALB | $0.0239/h, plus $0.008/LCU-h (near zero at demo traffic) | $0.0239 |
| Public IPv4 | $0.005/h each: two for the ALB, one for the task | $0.0150 |
| **Total** | | **≈ $0.052/h ≈ $1.25/day** |

Left up by mistake that is about $37 a month; the $11.50 budget covers about
nine days. Everything else is cents a month or inside the free tier:
Secrets Manager ($0.40 per secret-month, prorated), ECR storage, CloudWatch
Logs (7-day retention), S3, CloudFront, Lambda, SNS. Gemini usage is billed
by Google, not AWS.

The task size is a variable (`task_cpu`, `task_memory`). 0.25 vCPU / 512 MB
is chosen from a measurement, not a guess: two concurrent generations
peaked at 143 MiB and about 27% of one core in the local container. 0.5 vCPU /
1 GB would cost about $1.56/day.

## Budget and kill switch (`account/`)

- Budget `monthly-1000-inr`: $11.50/month (≈ ₹1,000; the Budgets API accepts
  only USD for this account), email alerts on actual spend at 50% and 75%.
- At **95%** the same email goes out and the budget also publishes to the
  kill switch's SNS topic. A Lambda then sets the ECS service to zero tasks
  and deletes the ALB -- the hourly costs above -- and emails a report of
  what it did. `make up` restores both. AWS Budgets' own actions cannot do
  this: they can attach a deny policy or stop EC2/RDS instances, not stop
  Fargate, an ALB or CloudFront.
- Budget data lags by several hours, so this is a backstop for a forgotten
  stack, not a real-time cap. `make down` after each demo is the real control.

The budget was created by hand first; `account/` adopts it with an `import`
block, so applying changes it in place rather than creating a second one.
The alert address comes from `account/terraform.tfvars` (gitignored; see
`terraform.tfvars.example`). AWS emails a confirmation link for the kill
switch's report topic once, after the first apply.

```bash
make account-plan    # read-only
make account-apply   # create the kill switch, attach it to the budget
make test-infra      # offline test of the Lambda's logic (fake boto3)
```

## Not built

- HTTPS between CloudFront and the ALB (needs a domain), and a custom domain
  for the demo.
- Authentication: by design the service sits behind a gateway that would do
  it (see `docs/ARCHITECTURE.md`). Today anyone with the CloudFront URL can
  use the demo while it is up; the in-process rate limit (6/min per client,
  burst 3) is the only brake.
- AWS WAF in front of CloudFront (it would add a monthly charge).
- Remote state, and CI/CD (Phase 6: build, push and deploy from GitHub
  Actions with OIDC instead of `make up` from a laptop).
