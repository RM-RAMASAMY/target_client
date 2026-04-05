# target_client

Simulated production system for the **SRE AI Agent** (CMPE 295B).

The service is a realistic e-commerce back-end API that an SRE AI agent can
monitor, detect production incidents in, and remediate via a REST control
plane.

---

## Architecture

```
┌────────────────────────────────────────┐
│              target_client             │
│                                        │
│  Business API  (/api/…)                │
│  Observability (/health  /readiness    │
│                 /metrics)              │
│  Incident API  (/incidents/…)          │
└────────────────────────────────────────┘
           ▲                  ▲
           │ scrape            │ REST
   Prometheus              SRE AI Agent
```

---

## Quick start

### Docker Compose (recommended)

```bash
docker compose up --build
```

Services:
| Service | URL |
|---|---|
| target_client | http://localhost:8080 |
| Prometheus | http://localhost:9090 |

### Local (no Docker)

```bash
pip install -r requirements.txt
python app.py
```

---

## API reference

### Business endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Service info & status |
| GET | `/api/products` | List all products |
| GET | `/api/products/<id>` | Get product by ID |
| GET | `/api/orders` | List all orders |
| POST | `/api/orders` | Create an order `{"product_id":1,"user_id":1,"quantity":2}` |
| GET | `/api/users` | List all users |
| GET | `/api/users/<id>` | Get user by ID |

### Observability endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness probe – 200 healthy / 503 unhealthy |
| GET | `/readiness` | Readiness probe – 200 ready / 503 not ready |
| GET | `/metrics` | Prometheus text metrics |

### Incident control plane

| Method | Path | Description |
|---|---|---|
| GET | `/incidents` | List active incidents & supported types |
| POST | `/incidents/<type>` | Start an incident (optional JSON body) |
| DELETE | `/incidents/<type>` | Resolve a specific incident |
| DELETE | `/incidents` | Resolve **all** active incidents |

---

## Incident catalogue

| Incident type | What it does | Optional params |
|---|---|---|
| `high_latency` | Adds artificial sleep to every request | `{"seconds": 2.0}` |
| `high_error_rate` | Returns HTTP 500 for a percentage of requests | `{"percentage": 50}` |
| `cpu_spike` | Starts a background CPU burn loop | — |
| `memory_leak` | Grows memory ~1 MB/s until resolved | — |
| `db_failure` | All DB-backed endpoints return 503 | — |
| `service_degraded` | Moderate latency + moderate error rate | — |
| `cascading_failure` | DB down + CPU spike + high latency + high errors | — |

### Example: start an incident

```bash
# Inject 3-second latency into every request
curl -X POST http://localhost:8080/incidents/high_latency \
     -H 'Content-Type: application/json' \
     -d '{"seconds": 3}'

# Check active incidents
curl http://localhost:8080/incidents

# Resolve the latency incident
curl -X DELETE http://localhost:8080/incidents/high_latency

# Resolve everything at once
curl -X DELETE http://localhost:8080/incidents
```

---

## Prometheus metrics

| Metric | Type | Description |
|---|---|---|
| `http_requests_total` | Counter | Requests by method, endpoint, status |
| `http_request_duration_seconds` | Histogram | Request latency |
| `active_incidents_total` | Gauge | Number of active incidents |
| `simulated_error_rate_percentage` | Gauge | Configured error injection rate |
| `simulated_extra_latency_seconds` | Gauge | Configured extra latency |
| `process_memory_rss_bytes` | Gauge | Process RSS memory |
| `process_cpu_percent` | Gauge | Process CPU usage |

---

## Running the tests

```bash
pip install pytest
pytest tests/test_app.py -v
```
