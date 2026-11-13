"""Offline test of the kill switch's logic, with boto3 replaced by fakes, so
it needs neither AWS access nor boto3 installed. Run from the repo root:

    backend/.venv/bin/python -m pytest -q infra/modules/kill_switch
"""

import importlib
import sys
import types

import pytest


class NotFound(Exception):
    pass


class FakeEcs:
    class exceptions:  # noqa: N801 -- mirrors boto3's client.exceptions
        ClusterNotFoundException = ServiceNotFoundException = ServiceNotActiveException = NotFound

    def __init__(self, state):
        self.state = state

    def update_service(self, cluster, service, desiredCount):
        if not self.state["service_exists"]:
            raise NotFound
        self.state["calls"].append(("update_service", cluster, service, desiredCount))


class FakeElb:
    class exceptions:  # noqa: N801
        LoadBalancerNotFoundException = NotFound

    def __init__(self, state):
        self.state = state

    def describe_load_balancers(self, Names):
        if self.state["elb_error"]:
            raise RuntimeError("throttled")
        if not self.state["alb_exists"]:
            raise NotFound
        return {"LoadBalancers": [{"LoadBalancerArn": f"arn:alb/{Names[0]}"}]}

    def delete_load_balancer(self, LoadBalancerArn):
        self.state["calls"].append(("delete_load_balancer", LoadBalancerArn))


class FakeSns:
    def __init__(self, state):
        self.state = state

    def publish(self, TopicArn, Subject, Message):
        self.state["published"].append((TopicArn, Subject, Message))


@pytest.fixture
def run(monkeypatch):
    state = {"service_exists": True, "alb_exists": True, "elb_error": False, "calls": [], "published": []}
    clients = {"ecs": FakeEcs, "elbv2": FakeElb, "sns": FakeSns}
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name: clients[name](state)))
    for key, value in {
        "CLUSTER_NAME": "sp-generator",
        "SERVICE_NAME": "sp-generator-api",
        "ALB_NAME": "sp-generator",
        "RESULTS_TOPIC_ARN": "arn:sns:results",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.syspath_prepend(__file__.rsplit("/", 1)[0])
    module = importlib.reload(importlib.import_module("kill_switch"))

    def invoke():
        event = {"Records": [{"Sns": {"Subject": "AWS Budgets: monthly-1000-inr has exceeded your alert threshold"}}]}
        return module.handler(event, None)

    return state, invoke


def test_stops_the_service_and_deletes_the_alb(run):
    state, invoke = run

    summary = invoke()

    assert ("update_service", "sp-generator", "sp-generator-api", 0) in state["calls"]
    assert ("delete_load_balancer", "arn:alb/sp-generator") in state["calls"]
    assert summary["trigger"] == ["AWS Budgets: monthly-1000-inr has exceeded your alert threshold"]
    (topic, subject, message), = state["published"]
    assert topic == "arn:sns:results" and "desired count set to 0" in message and "deleted" in message


def test_already_down_is_not_an_error(run):
    state, invoke = run
    state["service_exists"] = state["alb_exists"] = False

    summary = invoke()

    assert state["calls"] == []
    assert all("already down" in result for result in summary["results"])


def test_one_failing_step_does_not_stop_the_other_and_is_reported(run):
    state, invoke = run
    state["elb_error"] = True

    summary = invoke()

    assert ("update_service", "sp-generator", "sp-generator-api", 0) in state["calls"]
    assert any("FAILED: throttled" in result for result in summary["results"])
    assert "FAILED" in state["published"][0][2]
