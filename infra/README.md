# Infrastructure (AWS, Terraform)

Two Terraform root stacks in `ap-south-1` (Mumbai), built from small modules.
The demo is served at **https://sqlgen.abhishek.gen.in**.

| Stack | What | Lifetime |
|---|---|---|
| [`demo/`](demo/) | The service: network, ECS Fargate (API + UI) behind an HTTPS ALB, the DNS alias, the API-key secret | Up for a demo, destroyed after (`make up` / `make down`) |
| [`account/`](account/) | The DNS zone and TLS certificate; the ECR repositories; the GitHub deploy role; the monthly budget and its kill switch | Long-lived; `make down` never touches it |

| Module | |
|---|---|
| [`modules/network`](modules/network/) | VPC, two public subnets in two AZs, internet gateway. No NAT gateway |
| [`modules/ecr`](modules/ecr/) | Image repository: immutable tags, scan on push, keeps the newest few (3 here) |
| [`modules/service`](modules/service/) | ECS cluster, one task with `api` and `ui` containers, service; ALB with HTTPS and path routing, two target groups, security groups, execution role, log group |
| [`modules/domain`](modules/domain/) | Route 53 zone for the subdomain and its ACM certificate (DNS-validated) |
| [`modules/github_deploy`](modules/github_deploy/) | GitHub OIDC provider and a deploy role trusted only by this repository's `main` branch |
| [`modules/kill_switch`](modules/kill_switch/) | SNS topics and a Lambda that stops the hourly costs when the budget's top threshold is crossed |

State is **local** (`terraform.tfstate` in each stack directory, gitignored):
one owner, short-lived stacks. A shared S3 backend would be the next step for
more than one operator. Provider versions are pinned by the committed
`.terraform.lock.hcl` files.

## How requests flow

```
browser --HTTPS--> sqlgen.abhishek.gen.in (ALB, TLS 1.2/1.3)
                     +-- /v1/*, /api/* --> api container :8000   (FastAPI)
                     +-- everything else --> ui container :80     (nginx, the built UI)
                   http://... --301--> https://...
```

Both containers run in the same Fargate task. The browser sees one origin,
so there is no CORS, and the UI is built with an empty `VITE_API_BASE_URL`
so it calls `/v1/...` on whatever host served it. The ALB is the only thing
with an inbound path: tasks accept traffic only from its security group and
send only HTTPS out (ECR, Secrets Manager, CloudWatch Logs, Gemini). They
have public IPs because there is no NAT gateway. The API sees the client in
`X-Forwarded-For`, appended by the ALB, so `TRUSTED_PROXY_HOPS=1`.

Timeouts: the ALB's idle timeout is 300s and the app's generation timeout is
200s; the stream sends a heartbeat every 15s.

## DNS: the subdomain is delegated to Route 53

`abhishek.gen.in` stays at GoDaddy. Only `sqlgen.abhishek.gen.in` is handed
to a Route 53 zone (in the account stack), by four NS records added at
GoDaddy once. After that everything under the subdomain is managed here:
the certificate's validation record, and the alias to whichever ALB the
current `make up` created. The address never changes.

**First-time setup**, in order:

1. `make dns-zone` -- creates only the zone and prints its four name servers.
2. At GoDaddy: **My Products → abhishek.gen.in → DNS → Add New Record**, four
   times: type **NS**, name **sqlgen**, value one of the four name servers
   (without the trailing dot), TTL default. Leave every other record alone.
3. `make dns-check` until it lists the four `awsdns` servers (minutes to an
   hour or so, depending on caches).
4. `make account-apply` -- creates the certificate, its validation record,
   and waits for ACM to issue it (usually a few minutes once the delegation
   is visible; it gives up after 30 minutes).

## Usage

Prerequisites: the AWS CLI with an SSO profile `sp-generator`
(`aws sso login --profile sp-generator` when the session expires), Terraform
>= 1.10, Docker, `GEMINI_API_KEY` in the repo-root `.env`, and the DNS setup
above done once.

```bash
make plan     # what `make up` would create -- read-only
make up       # create everything and deploy (COSTS MONEY), prints the URL
make status   # up (with the URL) or down
make down     # destroy everything make up created
```

`make up` (`scripts/up.sh`) asks for approval once, on a plan of the whole
stack, then:

1. creates the stack with the service at **zero tasks** (and points the
   domain at the new ALB);
2. writes `GEMINI_API_KEY` from `.env` into Secrets Manager with
   `put-secret-value` -- the key never appears in Terraform state or plans;
3. builds the API image (the corpus is embedded at build time, with the key
   passed as a BuildKit secret) and the UI image, and pushes both to ECR
   tagged with the git commit;
4. scales the service to one task and waits until both target groups report
   it healthy.

`make down` deletes everything in the demo stack, including the secret (no
recovery window, so the next `make up` can reuse the name). The zone,
certificate and image repositories stay, so the next `make up` is served at
the same address with no DNS work, and skips building any image that CI (or
an earlier `make up`) already pushed for that commit.

## Continuous delivery (GitHub Actions)

| Workflow | When | What |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) | Pull requests and non-main branches (and called by `deploy.yml`) | ruff, the offline tests (unit, API, contract, kill switch), pip-audit and npm audit, `terraform fmt` and `validate` for both stacks, Docker builds |
| [`deploy.yml`](../.github/workflows/deploy.yml) | Every push to `main`, or by hand | CI, then build and push both images tagged with the commit, then roll the live service if the demo is up |
| [`evals.yml`](../.github/workflows/evals.yml) | By hand; nightly if `NIGHTLY_EVALS` is `true` | The live-API evals; results uploaded as an artifact |

**How a merge deploys.** `deploy.yml` assumes the deploy role through GitHub
OIDC -- a short-lived token, no AWS keys stored in GitHub; the role trusts
only `repo:abhishekar10/store-procedure-generator-service:ref:refs/heads/main`.
It runs `scripts/push-images.sh` (the same script `make up` uses), then
`scripts/roll-service.sh`: if the demo service exists, it registers a copy of
the current task definition with the two new images, points the service at
it and waits until it is stable; the ECS circuit breaker rolls back a
deployment that never becomes healthy. If the demo is down, it pushes the
images and stops -- the next `make up` deploys them.

The role can push to the two repositories and roll that one service; it
cannot create or destroy infrastructure. Terraform stays a laptop operation
(`make up` / `make down`) because its state is local. After a pipeline
deploy, the service runs a task definition Terraform did not create; the
next `make up` reconciles it.

**Repository settings** (Settings → Secrets and variables → Actions):

| Kind | Name | Value |
|---|---|---|
| Secret | `GEMINI_API_KEY` | the API key -- the image build embeds the corpus, and the evals call Gemini |
| Variable | `AWS_DEPLOY_ROLE_ARN` | `terraform -chdir=infra/account output -raw github_deploy_role_arn` |
| Variable | `AWS_REGION` | optional, defaults to `ap-south-1` |
| Variable | `NIGHTLY_EVALS` | optional, `true` to run the evals nightly |

Until `AWS_DEPLOY_ROLE_ARN` is set, `deploy.yml` runs CI and skips the
deploy job rather than failing.

## Cost

Hourly charges while the demo stack is up, from the AWS Price List API for
`ap-south-1` (checked 2026-11-14):

| | Rate | Per hour |
|---|---|---|
| Fargate, 0.25 vCPU + 0.5 GB (both containers) | $0.04256/vCPU-h, $0.004655/GB-h | $0.0130 |
| ALB | $0.0239/h, plus $0.008/LCU-h (near zero at demo traffic) | $0.0239 |
| Public IPv4 | $0.005/h each: two for the ALB, one for the task | $0.0150 |
| **Total** | | **≈ $0.052/h ≈ $1.25/day** |

Left up by mistake that is about $37 a month; the $11.50 budget covers about
nine days. Always-on, whether the demo is up or not: the Route 53 zone,
$0.50/month (plus $0.40 per million queries), and ECR storage at
$0.10/GB-month -- the newest 3 images of each repository (the API image is
880 MB uncompressed locally, less in ECR, compressed), so well under $0.30 a
month. Everything else is cents a month or inside the free tier: Secrets
Manager ($0.40 per secret-month, prorated), CloudWatch Logs (7-day
retention), Lambda, SNS. The certificate is free. GitHub Actions minutes are
free for public repositories. Gemini usage is billed by Google, not AWS.

The task size is a variable (`task_cpu`, `task_memory`). 0.25 vCPU / 512 MB
is chosen from a measurement, not a guess: two concurrent generations
peaked at 143 MiB and about 27% of one core in the local API container;
nginx serving static files adds a few MiB. 0.5 vCPU / 1 GB would cost about
$1.56/day.

## Budget and kill switch (`account/`)

- Budget `monthly-1000-inr`: $11.50/month (≈ ₹1,000; the Budgets API accepts
  only USD for this account), email alerts on actual spend at 50% and 75%.
- At **95%** the same email goes out and the budget also publishes to the
  kill switch's SNS topic. A Lambda then sets the ECS service to zero tasks
  and deletes the ALB -- the hourly costs above -- and emails a report of
  what it did. `make up` restores both. AWS Budgets' own actions cannot do
  this: they can attach a deny policy or stop EC2/RDS instances, not stop
  Fargate or an ALB. Tested live once with nothing deployed (it reported
  both targets "already down"); not yet run against a live stack.
- Budget data lags by several hours, so this is a backstop for a forgotten
  stack, not a real-time cap. `make down` after each demo is the real control.

The budget was created by hand first; `account/` adopts it with an `import`
block. The alert address comes from `account/terraform.tfvars` (gitignored;
see `terraform.tfvars.example`).

```bash
make account-plan    # read-only
make account-apply   # create/update the zone, certificate, kill switch, budget
make test-infra      # offline test of the Lambda's logic (fake boto3)
```

## History: why not CloudFront

The first design put CloudFront in front of both the UI (from S3) and the
API, because without a domain the ALB could only serve HTTP. The first
`make up` failed there: new AWS accounts cannot create CloudFront
distributions until AWS Support verifies them. With a domain available, the
ALB serves HTTPS itself, which removes CloudFront, S3 and CloudFront's
60-second origin timeout, and gives a stable address. CloudFront could
return later as a CDN in front of the ALB.

## Not built

- Authentication: by design the service sits behind a gateway that would do
  it (see `docs/ARCHITECTURE.md`). Today anyone with the URL can use the demo
  while it is up; the in-process rate limit (6/min per client, burst 3) is
  the only brake.
- AWS WAF on the ALB (it would add a monthly charge).
- Remote state. With it, the pipeline could run Terraform too (bring the
  stack up and down from GitHub); today that stays on the owner's machine.
