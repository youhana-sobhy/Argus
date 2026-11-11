import os
import tempfile

# Isolate every test run: fresh runtime dir, mock connector, offline AI.
_tmp = tempfile.mkdtemp(prefix="argus-test-")
os.environ["ARGUS_RUNTIME_DIR"] = _tmp
os.environ["ARGUS_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ARGUS_CONNECTOR"] = "mock"
os.environ["ARGUS_LLM_PROVIDER"] = "mock"
os.environ["ARGUS_DEMO_PASSWORD"] = "test-pass"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def app():
    from argus.main import app as _app

    return _app


@pytest.fixture()
def login(app):
    def _login(username: str) -> TestClient:
        c = TestClient(app)
        c.__enter__()
        r = c.post("/api/auth/login", json={"username": username, "password": "test-pass"})
        assert r.status_code == 200, r.text
        return c

    return _login


@pytest.fixture(autouse=True)
def reset_lab():
    from argus.core.inventory import get_inventory

    inv = get_inventory()
    inv.connector.reset()
    inv.invalidate()
    yield
