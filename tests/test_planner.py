"""Planner, mock APIC and rollback behaviour."""

from argus.changes import planner
from argus.changes.intents import parse_intent
from argus.connectors import aci_mo
from argus.core.inventory import get_inventory


def _plan(data):
    inv = get_inventory()
    intent = parse_intent(data)
    return intent, planner.plan(intent, inv.snapshot(refresh=True), inv.native()), inv


def test_normalizer_counts():
    st = get_inventory().snapshot(refresh=True).stats()
    assert st["tenants"] == 4 and st["spines"] == 2 and st["leaves"] == 6 and st["bridge_domains"] == 10


def test_create_bd_execute_and_rollback():
    intent, plan, inv = _plan({"kind": "create_bridge_domain", "tenant": "VF-CORE", "name": "CACHE_BD",
                                "vrf": "PROD_VRF", "subnets": ["10.10.40.1/24"]})
    assert plan["passed"] and plan["risk"] == "low"
    assert "CACHE_BD" in plan["diff"]
    inv.connector.apply(plan["payload"])
    assert all(c["status"] == "pass" for c in planner.verify(intent, inv.snapshot(refresh=True)))
    inv.connector.apply(plan["rollback_payload"])
    assert all(c["status"] == "pass" for c in planner.verify(intent, inv.snapshot(refresh=True), rolled_back=True))


def test_subnet_overlap_blocked():
    _, plan, _ = _plan({"kind": "create_bridge_domain", "tenant": "VF-CORE", "name": "X_BD", "vrf": "PROD_VRF",
                        "subnets": ["10.10.10.129/25"]})
    assert not plan["passed"]
    assert any(c["id"] == "subnet.overlap" and c["status"] == "fail" for c in plan["checks"])


def test_network_address_as_gateway_blocked():
    _, plan, _ = _plan({"kind": "create_bridge_domain", "tenant": "VF-CORE", "name": "Y_BD", "vrf": "PROD_VRF",
                        "subnets": ["10.10.77.0/24"]})
    assert not plan["passed"]


def test_delete_bd_in_use_blocked():
    _, plan, _ = _plan({"kind": "delete_object", "tenant": "VF-5GC", "object_type": "bridge_domain", "name": "AMF_BD"})
    assert not plan["passed"]
    assert any(c["id"] == "bd.in_use" for c in plan["checks"] if c["status"] == "fail")


def test_delete_ap_rollback_restores_epgs():
    intent, plan, inv = _plan({"kind": "delete_object", "tenant": "VF-CORE", "object_type": "app_profile", "name": "ECOMMERCE_AP"})
    assert plan["passed"] and plan["risk"] == "high"
    before = inv.snapshot(refresh=True).tenant("VF-CORE").ap("ECOMMERCE_AP").model_dump()
    inv.connector.apply(plan["payload"])
    assert inv.snapshot(refresh=True).tenant("VF-CORE").ap("ECOMMERCE_AP") is None
    inv.connector.apply(plan["rollback_payload"])
    assert inv.snapshot(refresh=True).tenant("VF-CORE").ap("ECOMMERCE_AP").model_dump() == before


def test_create_ap_with_missing_bd_blocked():
    _, plan, _ = _plan({"kind": "create_app_profile", "tenant": "VF-5GC", "name": "NRF_AP",
                        "epgs": [{"name": "NRF_EPG", "bridge_domain": "NOPE_BD"}]})
    assert not plan["passed"]


def test_protected_tenant_delete_blocked():
    _, plan, _ = _plan({"kind": "delete_object", "tenant": "common", "object_type": "bridge_domain", "name": "default"})
    assert not plan["passed"]


def test_mock_rejects_duplicate_create():
    inv = get_inventory()
    payload = aci_mo.mo("fvTenant", {"dn": "uni/tn-VF-CORE", "name": "VF-CORE"}, [aci_mo.mo("fvBD", {"name": "WEB_BD", "status": "created"})])
    try:
        inv.connector.apply(payload)
    except Exception as exc:  # noqa: BLE001
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate create should fail")


def test_export_roundtrip():
    snap = get_inventory().snapshot(refresh=True)
    exported = aci_mo.export_apic(snap)
    again = aci_mo.normalize({"fvTenant": exported["imdata"]}, target=snap.target)
    assert [t.model_dump() for t in again.tenants] == [t.model_dump() for t in snap.tenants]
