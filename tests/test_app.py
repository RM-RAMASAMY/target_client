"""
Tests for the simulated production system (target_client).

Run with:  pytest tests/test_app.py -v
"""

import time
import pytest

# Import the Flask app in test mode
import incidents as inc
from app import app


@pytest.fixture(autouse=True)
def reset_incidents():
    """Ensure all incidents are cleared before and after every test."""
    inc.resolve_all()
    yield
    inc.resolve_all()


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Root / observability
# ---------------------------------------------------------------------------

class TestRoot:
    def test_index(self, client):
        r = client.get("/")
        assert r.status_code == 200
        data = r.get_json()
        assert data["service"] == "target_client"
        assert data["status"] == "ok"

    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.get_json()["status"] == "healthy"

    def test_readiness_ok(self, client):
        r = client.get("/readiness")
        assert r.status_code == 200
        assert r.get_json()["status"] == "ready"

    def test_metrics_endpoint(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert b"http_requests_total" in r.data


# ---------------------------------------------------------------------------
# Business API – happy path
# ---------------------------------------------------------------------------

class TestBusinessAPI:
    def test_list_products(self, client):
        r = client.get("/api/products")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data["products"]) == 5

    def test_get_product(self, client):
        r = client.get("/api/products/1")
        assert r.status_code == 200
        assert r.get_json()["name"] == "Widget Pro"

    def test_get_product_not_found(self, client):
        r = client.get("/api/products/999")
        assert r.status_code == 404

    def test_list_users(self, client):
        r = client.get("/api/users")
        assert r.status_code == 200
        assert len(r.get_json()["users"]) == 3

    def test_get_user(self, client):
        r = client.get("/api/users/1")
        assert r.status_code == 200
        assert r.get_json()["username"] == "alice"

    def test_get_user_not_found(self, client):
        r = client.get("/api/users/999")
        assert r.status_code == 404

    def test_list_orders_empty(self, client):
        r = client.get("/api/orders")
        assert r.status_code == 200
        assert r.get_json()["total"] == 0

    def test_create_order(self, client):
        r = client.post(
            "/api/orders",
            json={"product_id": 1, "user_id": 1, "quantity": 2},
        )
        assert r.status_code == 201
        order = r.get_json()
        assert order["product_id"] == 1
        assert order["total"] == pytest.approx(59.98, rel=1e-4)

    def test_create_order_missing_fields(self, client):
        r = client.post("/api/orders", json={"product_id": 1})
        assert r.status_code == 400

    def test_create_order_product_not_found(self, client):
        r = client.post(
            "/api/orders",
            json={"product_id": 999, "user_id": 1},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Incident management API
# ---------------------------------------------------------------------------

class TestIncidentAPI:
    def test_list_incidents_empty(self, client):
        r = client.get("/incidents")
        assert r.status_code == 200
        data = r.get_json()
        assert data["active"] == {}
        assert "high_latency" in data["supported"]

    def test_start_unknown_incident(self, client):
        r = client.post("/incidents/blah_blah")
        assert r.status_code == 400

    def test_resolve_nonexistent_incident(self, client):
        r = client.delete("/incidents/high_latency")
        assert r.status_code == 404

    def test_start_and_resolve_high_error_rate(self, client):
        # Start
        r = client.post("/incidents/high_error_rate", json={"percentage": 100})
        assert r.status_code == 201
        assert r.get_json()["started"] == "high_error_rate"

        # Check active
        r = client.get("/incidents")
        assert "high_error_rate" in r.get_json()["active"]

        # Resolve
        r = client.delete("/incidents/high_error_rate")
        assert r.status_code == 200
        assert r.get_json()["resolved"] == "high_error_rate"

        # No longer active
        r = client.get("/incidents")
        assert "high_error_rate" not in r.get_json()["active"]

    def test_resolve_all(self, client):
        client.post("/incidents/high_latency", json={"seconds": 0.001})
        client.post("/incidents/db_failure")
        r = client.delete("/incidents")
        assert r.status_code == 200
        assert len(r.get_json()["resolved_all"]) >= 2
        r = client.get("/incidents")
        assert r.get_json()["active"] == {}


# ---------------------------------------------------------------------------
# Incident behaviour
# ---------------------------------------------------------------------------

class TestIncidentBehaviour:
    def test_high_error_rate_causes_500s(self, client):
        """With 100% error rate every business request returns 500."""
        inc.start_incident("high_error_rate", {"percentage": 100})
        for _ in range(5):
            r = client.get("/api/products")
            assert r.status_code == 500

    def test_db_failure_causes_503(self, client):
        inc.start_incident("db_failure")
        r = client.get("/api/products")
        assert r.status_code == 503

    def test_db_failure_readiness_probe(self, client):
        inc.start_incident("db_failure")
        r = client.get("/readiness")
        assert r.status_code == 503

    def test_high_latency_slows_requests(self, client):
        inc.start_incident("high_latency", {"seconds": 0.2})
        start = time.time()
        client.get("/api/products")
        elapsed = time.time() - start
        assert elapsed >= 0.2

    def test_service_degraded_index_status(self, client):
        inc.start_incident("service_degraded")
        r = client.get("/")
        assert r.get_json()["status"] == "degraded"

    def test_cascading_failure(self, client):
        inc.start_incident("cascading_failure")
        # DB is down and/or error rate is injected -> products return 5xx
        r = client.get("/api/products")
        assert r.status_code in (500, 503)
        # Readiness should fail because DB is down
        r = client.get("/readiness")
        assert r.status_code == 503

    def test_resolve_restores_normal(self, client):
        inc.start_incident("high_error_rate", {"percentage": 100})
        r = client.get("/api/products")
        assert r.status_code == 500

        inc.resolve_incident("high_error_rate")
        # After resolving, requests succeed again
        success = False
        for _ in range(10):
            r = client.get("/api/products")
            if r.status_code == 200:
                success = True
                break
        assert success
