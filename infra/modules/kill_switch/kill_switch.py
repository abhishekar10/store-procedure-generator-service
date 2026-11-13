"""Budget kill switch: stops what costs money by the hour in the demo stack.

Triggered by SNS when the account budget's kill-switch threshold is crossed.
Sets the ECS service's desired count to 0 (no Fargate tasks, no task public
IPs) and deletes the ALB (its hourly charge and public IPs). Everything else
the stack holds costs cents a month. `make up` recreates both: Terraform sees
the ALB missing and the count changed.

Each step is independent and tolerates the resource not existing, so it is
safe to run when the stack is already down, or twice. The outcome is
published to a results topic, which emails the owner.
"""

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLUSTER = os.environ["CLUSTER_NAME"]
SERVICE = os.environ["SERVICE_NAME"]
ALB = os.environ["ALB_NAME"]
RESULTS_TOPIC = os.environ["RESULTS_TOPIC_ARN"]


def _stop_service(ecs) -> str:
    try:
        ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)
        return f"ECS service {CLUSTER}/{SERVICE}: desired count set to 0"
    except (ecs.exceptions.ClusterNotFoundException, ecs.exceptions.ServiceNotFoundException,
            ecs.exceptions.ServiceNotActiveException):
        return f"ECS service {CLUSTER}/{SERVICE}: not found (already down)"


def _delete_alb(elb) -> str:
    try:
        balancers = elb.describe_load_balancers(Names=[ALB])["LoadBalancers"]
    except elb.exceptions.LoadBalancerNotFoundException:
        return f"ALB {ALB}: not found (already down)"
    for balancer in balancers:
        elb.delete_load_balancer(LoadBalancerArn=balancer["LoadBalancerArn"])
    return f"ALB {ALB}: deleted"


def handler(event, _context):
    trigger = [record.get("Sns", {}).get("Subject") for record in event.get("Records", [])]
    results = []
    for step, client in ((_stop_service, "ecs"), (_delete_alb, "elbv2")):
        try:
            results.append(step(boto3.client(client)))
        except Exception as exc:  # noqa: BLE001 -- report every step, even if one fails
            logger.exception("kill switch step failed")
            results.append(f"{step.__name__} FAILED: {exc}")

    summary = {"trigger": trigger, "results": results}
    logger.info(json.dumps(summary))
    boto3.client("sns").publish(
        TopicArn=RESULTS_TOPIC,
        Subject="Budget kill switch ran",
        Message="The budget kill switch ran because the budget threshold was crossed.\n\n"
        + "\n".join(results)
        + "\n\nBring the stack back with `make up`, or leave it down.",
    )
    return summary
