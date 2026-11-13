# A subdomain delegated to Route 53, and a TLS certificate for it.
#
# The parent domain stays where it is (here GoDaddy). The parent's DNS gets
# NS records for the subdomain pointing at this zone's name servers -- added
# by hand, once -- and from then on everything under the subdomain is managed
# here: the certificate's validation record now, and the demo stack's alias
# to its load balancer on every `make up`.
#
# Long-lived on purpose: the zone's name servers must not change (they are
# pasted into the parent's DNS), and an ACM certificate is free and renews
# itself as long as its validation record stays in place.

resource "aws_route53_zone" "this" {
  name    = var.domain_name
  comment = "Delegated from the parent domain's DNS by NS records"
}

resource "aws_acm_certificate" "this" {
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "validation" {
  for_each = {
    for option in aws_acm_certificate.this.domain_validation_options : option.domain_name => option
  }

  zone_id = aws_route53_zone.this.zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 300
}

# Waits until ACM sees the validation record, which needs the NS delegation
# in the parent's DNS to be in place first (see infra/README.md).
resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for record in aws_route53_record.validation : record.fqdn]

  timeouts {
    create = "30m"
  }
}
