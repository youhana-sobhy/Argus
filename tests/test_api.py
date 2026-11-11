"""End-to-end API: auth, RBAC, four-eyes, lifecycle, audit."""

BD = {"kind": "create_bridge_domain", "tenant": "VF-CORE", "name": "API_BD", "vrf": "PROD_VRF", "subnets": ["10.10.60.1/24"]}


def test_login_required(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        assert c.get("/api/inventory/snapshot").status_code == 401
        assert c.get("/", follow_redirects=False).status_code == 303
        assert c.post("/api/auth/login", json={"username": "engineer", "password": "wrong"}).status_code == 401


def test_full_lifecycle(login):
    eng, appr, view = login("engineer"), login("approver"), login("viewer")
    cr = eng.post("/api/changes", json={"intent": BD, "justification": "test"}).json()
    assert cr["status"] == "validated"
    cid = cr["id"]
    assert view.post(f"/api/changes/{cid}/submit", json={}).status_code == 403
    assert eng.post(f"/api/changes/{cid}/submit", json={}).json()["status"] == "pending_approval"
    assert eng.post(f"/api/changes/{cid}/approve", json={}).status_code == 403  # engineer role
    assert appr.post(f"/api/changes/{cid}/approve", json={"note": "ok"}).json()["status"] == "approved"
    done = eng.post(f"/api/changes/{cid}/execute", json={}).json()
    assert done["status"] == "completed"
    assert all(c["status"] == "pass" for c in done["result"]["post_checks"])
    assert eng.post(f"/api/changes/{cid}/rollback", json={}).json()["status"] == "rolled_back"
    actions = [e["action"] for e in appr.get("/api/audit").json()]
    for a in ("change.create", "change.approve", "change.execute", "change.rollback"):
        assert a in actions


def test_four_eyes(login):
    admin = login("admin")
    cid = admin.post("/api/changes", json={"intent": BD}).json()["id"]
    admin.post(f"/api/changes/{cid}/submit", json={})
    r = admin.post(f"/api/changes/{cid}/approve", json={})
    assert r.status_code == 400 and "Four-eyes" in r.json()["detail"]


def test_cannot_execute_unapproved(login):
    eng = login("engineer")
    cid = eng.post("/api/changes", json={"intent": BD}).json()["id"]
    assert eng.post(f"/api/changes/{cid}/execute", json={}).status_code == 400


def test_chat_offline_proposal(login):
    eng = login("engineer")
    r = eng.post("/api/chat", json={"question": "delete LEGACY_BD"}).json()
    assert r["proposal"]["kind"] == "delete_object" and r["provider"] == "offline"


def test_exports(login):
    eng = login("engineer")
    for fmt in ("normalized", "apic", "native"):
        r = eng.get(f"/api/inventory/export?format={fmt}")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert login("viewer").get("/api/inventory/export").status_code == 403


def test_pages_render(login):
    eng = login("engineer")
    for p in ("/", "/topology", "/inventory", "/assistant", "/changes", "/changes/new"):
        assert eng.get(p).status_code == 200, p
    assert eng.get("/audit", follow_redirects=False).status_code == 303  # engineer has no audit.read


def test_present_page_is_public(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        r = c.get("/present", follow_redirects=False)
        assert r.status_code == 200 and "AutoHero" in r.text
