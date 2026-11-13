# AWS demo stack. Profile and region default to sp-generator / ap-south-1;
# override with AWS_PROFILE=... AWS_REGION=...
#
#   make plan            show what `make up` would create (read-only)
#   make up              create the stack, deploy the API and UI  (COSTS MONEY)
#   make down            destroy the stack
#   make status          is it up, and at what URL
#   make dns-zone        first time only: create the DNS zone, print the NS records for GoDaddy
#   make dns-check       has the NS delegation reached public DNS yet?
#   make account-plan    the budget, kill switch, DNS zone and certificate (read-only)
#   make account-apply   create/update them (the certificate waits for the delegation)
#   make test-infra      offline test of the kill-switch function

export AWS_PROFILE ?= sp-generator
TF_DEMO    = terraform -chdir=infra/demo
TF_ACCOUNT = terraform -chdir=infra/account

DOMAIN ?= sqlgen.abhishek.gen.in

.PHONY: plan up down status dns-zone dns-check account-plan account-apply test-infra fmt validate

plan:
	$(TF_DEMO) init -input=false >/dev/null
	$(TF_DEMO) plan -input=false -var image_tag=$$(git rev-parse --short=12 HEAD)

up:
	infra/scripts/up.sh

down:
	infra/scripts/down.sh

status:
	@url=$$($(TF_DEMO) output -raw url 2>/dev/null); \
	if [ -n "$$url" ]; then echo "up: $$url"; else echo "down (no demo stack in state)"; fi

dns-zone:
	$(TF_ACCOUNT) init -input=false >/dev/null
	$(TF_ACCOUNT) apply -input=false -target=module.domain.aws_route53_zone.this
	@echo; echo "Add these as NS records named 'sqlgen' at GoDaddy:"; $(TF_ACCOUNT) output -json name_servers

dns-check:
	@echo "Public DNS (8.8.8.8) for $(DOMAIN) NS -- should list the four awsdns servers:"
	@dig +short NS $(DOMAIN) @8.8.8.8

account-plan:
	$(TF_ACCOUNT) init -input=false >/dev/null
	$(TF_ACCOUNT) plan -input=false

account-apply:
	$(TF_ACCOUNT) init -input=false >/dev/null
	$(TF_ACCOUNT) apply -input=false

test-infra:
	backend/.venv/bin/python -m pytest -q -p no:cacheprovider infra/modules/kill_switch

fmt:
	terraform fmt -recursive infra

validate:
	$(TF_DEMO) validate && $(TF_ACCOUNT) validate
