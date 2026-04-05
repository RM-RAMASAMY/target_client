"""
Simulated production web service – target client for an SRE AI agent.

Endpoints
---------
Business API (simulates an e-commerce backend)
  GET  /                         service info
  GET  /api/products             list products
  GET  /api/products/<id>        get product
  GET  /api/orders               list orders
  POST /api/orders               create order
  GET  /api/users                list users
  GET  /api/users/<id>           get user

Observability
  GET  /health                   liveness probe  (200 / 503)
  GET  /readiness                readiness probe (200 / 503)
  GET  /metrics                  Prometheus text metrics

Incident control (for the SRE agent / test harness)
  GET  /incidents                list active incidents
  POST /incidents/<type>         start an incident
  DELETE /incidents/<type>       resolve an incident
  DELETE /incidents              resolve all incidents
"""

import os
import random
import time
import threading
import psutil
import logging

from flask import Flask, jsonify, request, Response
from prometheus_client import (
    Counter, Histogram, Gauge,
    generate_latest, CONTENT_TYPE_LATEST,
)

import incidents as inc

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("target_client")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10],
)
ACTIVE_INCIDENTS_GAUGE = Gauge(
    "active_incidents_total",
    "Number of currently active incidents",
)
ERROR_RATE_GAUGE = Gauge(
    "simulated_error_rate_percentage",
    "Configured simulated error rate (%)",
)
LATENCY_GAUGE = Gauge(
    "simulated_extra_latency_seconds",
    "Configured extra latency injected per request (s)",
)
MEMORY_GAUGE = Gauge(
    "process_memory_rss_bytes",
    "Process RSS memory in bytes",
)
CPU_GAUGE = Gauge(
    "process_cpu_percent",
    "Process CPU usage percent (last interval)",
)

# ---------------------------------------------------------------------------
# Fake data store
# ---------------------------------------------------------------------------
PRODUCTS = [
    {"id": 1, "name": "Widget Pro", "price": 29.99, "stock": 150},
    {"id": 2, "name": "Gadget Plus", "price": 49.99, "stock": 80},
    {"id": 3, "name": "Doohickey Max", "price": 9.99, "stock": 500},
    {"id": 4, "name": "Thingamajig XL", "price": 99.99, "stock": 20},
    {"id": 5, "name": "Whatchamacallit", "price": 14.99, "stock": 300},
]

USERS = [
    {"id": 1, "username": "alice", "email": "alice@example.com", "tier": "gold"},
    {"id": 2, "username": "bob",   "email": "bob@example.com",   "tier": "silver"},
    {"id": 3, "username": "carol", "email": "carol@example.com", "tier": "bronze"},
]

_orders = []
_order_counter = 0
_orders_lock = threading.Lock()


def _next_order_id():
    global _order_counter
    _order_counter += 1
    return _order_counter


# ---------------------------------------------------------------------------
# Middleware helpers
# ---------------------------------------------------------------------------

def _maybe_inject_fault() -> bool:
    """Return True if the current request should be failed (5xx)."""
    pct = inc.error_rate_pct
    if pct > 0 and random.random() < pct / 100.0:
        return True
    return False


def _inject_latency():
    extra = inc.high_latency_seconds
    if extra > 0:
        time.sleep(extra)


def _db_call(operation: str) -> None:
    """Simulate a DB call; raises RuntimeError if db_failure is active."""
    if inc.db_failure_active:
        raise RuntimeError(f"DB connection refused during {operation!r}")
    # Normal DB latency (5–20 ms)
    time.sleep(random.uniform(0.005, 0.020))


# ---------------------------------------------------------------------------
# Background metrics updater
# ---------------------------------------------------------------------------

def _metrics_updater():
    proc = psutil.Process(os.getpid())
    while True:
        try:
            MEMORY_GAUGE.set(proc.memory_info().rss)
            CPU_GAUGE.set(proc.cpu_percent(interval=None))
            ACTIVE_INCIDENTS_GAUGE.set(len(inc.list_incidents()))
            ERROR_RATE_GAUGE.set(inc.error_rate_pct)
            LATENCY_GAUGE.set(inc.high_latency_seconds)
        except Exception:
            pass
        time.sleep(5)


threading.Thread(target=_metrics_updater, daemon=True, name="metrics-updater").start()


# ---------------------------------------------------------------------------
# Request lifecycle hooks
# ---------------------------------------------------------------------------

@app.before_request
def before():
    request._start_time = time.time()


@app.after_request
def after(response):
    elapsed = time.time() - getattr(request, "_start_time", time.time())
    endpoint = request.endpoint or "unknown"
    REQUEST_COUNT.labels(request.method, endpoint, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(request.method, endpoint).observe(elapsed)
    logger.info(
        "%s %s -> %s (%.3fs)",
        request.method, request.path, response.status_code, elapsed,
    )
    return response


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return jsonify({
        "service": "target_client",
        "description": "Simulated production system for SRE AI agent",
        "version": "1.0.0",
        "status": "degraded" if (inc.service_degraded or inc.db_failure_active) else "ok",
        "active_incidents": len(inc.list_incidents()),
    })


# ---------------------------------------------------------------------------
# Business endpoints
# ---------------------------------------------------------------------------

@app.route("/api/products")
def list_products():
    _inject_latency()
    if _maybe_inject_fault():
        return jsonify({"error": "Internal server error"}), 500
    try:
        _db_call("list_products")
    except RuntimeError:
        return jsonify({"error": "Service unavailable: database error"}), 503
    return jsonify({"products": PRODUCTS, "total": len(PRODUCTS)})


@app.route("/api/products/<int:product_id>")
def get_product(product_id):
    _inject_latency()
    if _maybe_inject_fault():
        return jsonify({"error": "Internal server error"}), 500
    try:
        _db_call("get_product")
    except RuntimeError:
        return jsonify({"error": "Service unavailable: database error"}), 503
    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if product is None:
        return jsonify({"error": "Product not found"}), 404
    return jsonify(product)


@app.route("/api/orders", methods=["GET"])
def list_orders():
    _inject_latency()
    if _maybe_inject_fault():
        return jsonify({"error": "Internal server error"}), 500
    try:
        _db_call("list_orders")
    except RuntimeError:
        return jsonify({"error": "Service unavailable: database error"}), 503
    with _orders_lock:
        return jsonify({"orders": list(_orders), "total": len(_orders)})


@app.route("/api/orders", methods=["POST"])
def create_order():
    _inject_latency()
    if _maybe_inject_fault():
        return jsonify({"error": "Internal server error"}), 500
    try:
        _db_call("create_order")
    except RuntimeError:
        return jsonify({"error": "Service unavailable: database error"}), 503

    body = request.get_json(silent=True) or {}
    product_id = body.get("product_id")
    user_id = body.get("user_id")
    quantity = int(body.get("quantity", 1))

    if product_id is None or user_id is None:
        return jsonify({"error": "product_id and user_id are required"}), 400

    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if product is None:
        return jsonify({"error": "Product not found"}), 404

    order = {
        "id": _next_order_id(),
        "user_id": user_id,
        "product_id": product_id,
        "quantity": quantity,
        "total": round(product["price"] * quantity, 2),
        "status": "pending",
    }
    with _orders_lock:
        _orders.append(order)
    return jsonify(order), 201


@app.route("/api/users")
def list_users():
    _inject_latency()
    if _maybe_inject_fault():
        return jsonify({"error": "Internal server error"}), 500
    try:
        _db_call("list_users")
    except RuntimeError:
        return jsonify({"error": "Service unavailable: database error"}), 503
    return jsonify({"users": USERS, "total": len(USERS)})


@app.route("/api/users/<int:user_id>")
def get_user(user_id):
    _inject_latency()
    if _maybe_inject_fault():
        return jsonify({"error": "Internal server error"}), 500
    try:
        _db_call("get_user")
    except RuntimeError:
        return jsonify({"error": "Service unavailable: database error"}), 503
    user = next((u for u in USERS if u["id"] == user_id), None)
    if user is None:
        return jsonify({"error": "User not found"}), 404
    return jsonify(user)


# ---------------------------------------------------------------------------
# Observability endpoints
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    """Liveness probe – fails when a cascading/service_degraded incident is active."""
    if inc.service_degraded and inc.db_failure_active and inc.cpu_spike_active:
        return jsonify({"status": "unhealthy", "reason": "cascading failure"}), 503
    return jsonify({"status": "healthy"})


@app.route("/readiness")
def readiness():
    """Readiness probe – fails when the service cannot serve traffic."""
    if inc.db_failure_active:
        return jsonify({"status": "not_ready", "reason": "database unavailable"}), 503
    if inc.error_rate_pct >= 90:
        return jsonify({"status": "not_ready", "reason": "error rate >= 90%"}), 503
    return jsonify({"status": "ready"})


@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# Incident management endpoints
# ---------------------------------------------------------------------------

SUPPORTED_INCIDENTS = [
    "high_latency",
    "high_error_rate",
    "cpu_spike",
    "memory_leak",
    "db_failure",
    "service_degraded",
    "cascading_failure",
]


@app.route("/incidents", methods=["GET"])
def get_incidents():
    return jsonify({
        "active": inc.list_incidents(),
        "supported": SUPPORTED_INCIDENTS,
    })


@app.route("/incidents/<incident_type>", methods=["POST"])
def start_incident(incident_type):
    params = request.get_json(silent=True) or {}
    try:
        result = inc.start_incident(incident_type, params)
    except ValueError:
        return jsonify({"error": f"Unknown or invalid incident type: {incident_type!r}"}), 400
    logger.warning("INCIDENT STARTED: %s params=%s", incident_type, params)
    return jsonify({"started": incident_type, "incident": result}), 201


@app.route("/incidents/<incident_type>", methods=["DELETE"])
def resolve_incident(incident_type):
    try:
        result = inc.resolve_incident(incident_type)
    except ValueError:
        return jsonify({"error": f"No active incident of type: {incident_type!r}"}), 404
    logger.warning("INCIDENT RESOLVED: %s", incident_type)
    return jsonify(result)


@app.route("/incidents", methods=["DELETE"])
def resolve_all_incidents():
    result = inc.resolve_all()
    logger.warning("ALL INCIDENTS RESOLVED")
    return jsonify(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
