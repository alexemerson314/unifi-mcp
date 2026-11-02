"""Caller-supplied ids and controller request paths that a URL parser would re-target."""

import pytest
from unifi_core.request_paths import InvalidRequestPathError, check_request_path, is_safe_request_path, object_id


@pytest.mark.parametrize(
    "value",
    [
        "5f1e2d3c4b5a697887766554",  # Mongo ObjectID
        "0b8f3c1e-7d2a-4c5b-9e6f-1a2b3c4d5e6f",  # UUID
        "aa:bb:cc:dd:ee:ff",  # MAC
        "AA:BB:CC:DD:EE:FF",
        "super_mgmt",  # settings key
    ],
)
def test_object_id_accepts_controller_id_forms(value):
    assert object_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "../firewallrule/5f1e2d3c4b5a697887766554",
        "..",
        ".",
        "abc/def",
        "%2e%2e",
        "abc?x=1",
        "..;",
        "abc;jsessionid=x",
        "abc#frag",
        "abc\\def",
        "abc def",
        "abc\n",
        "",
        None,
        123,
    ],
)
def test_object_id_rejects_values_that_are_not_one_segment(value):
    with pytest.raises(InvalidRequestPathError) as error:
        object_id(value, "firewall group")
    assert str(error.value) == "Invalid firewall group id"


def test_object_id_error_is_a_value_error():
    with pytest.raises(ValueError):
        object_id("../x")


@pytest.mark.parametrize(
    "path",
    [
        "/rest/portforward/5f1e2d3c4b5a697887766554",
        "/stat/device/aa:bb:cc:dd:ee:ff",
        "/get/setting/",
        "/stat/report/hourly.user",
        "/traffic-flow-latest-statistics?period=DAY&top=10",
        "/stat/alarm?archived=true",
        "/traffic?start=1&end=2&path=../ignored",  # dot segments in the query are not path segments
        "/stat/alarm?note=a;b",  # a ; in the query is not a path parameter
    ],
)
def test_safe_request_paths(path):
    assert is_safe_request_path(path)
    check_request_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/rest/portforward/../firewallrule/5f1e2d3c4b5a697887766554",
        "/rest/portforward/./5f1e2d3c4b5a697887766554",
        "/rest/portforward/..",
        "/rest/portforward/%2e%2e/firewallrule/x",
        "/rest/portforward/%2E%2E/firewallrule/x",
        "/rest/portforward/..%2ffirewallrule",
        "/rest/portforward/..;/firewallrule/5f1e2d3c4b5a697887766554",  # Tomcat treats ..; as ..
        "/rest/portforward/.;/5f1e2d3c4b5a697887766554",
        "/rest/portforward/x;jsessionid=abc",
        "/stat/alarm;x=1?archived=true",
        "/rest/portforward/a\\..\\b",
        "/rest/portforward/a#b",
        "/stat/alarm?archived=true#frag",
        "/rest/portforward/a b",
        "/rest/portforward/a\tb",
        None,
    ],
)
def test_unsafe_request_paths(path):
    assert not is_safe_request_path(path)
    with pytest.raises(InvalidRequestPathError, match="^Invalid controller request path$"):
        check_request_path(path)
