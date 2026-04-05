"""
Incident simulation engine.

Each incident type modifies global state flags that the main app reads to
produce the appropriate degraded behaviour.
"""

import threading
import time
import math
import os
import gc
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# ---------------------------------------------------------------------------
# Shared state – all incidents live here
# ---------------------------------------------------------------------------
_lock = threading.Lock()

_active_incidents: Dict[str, Dict[str, Any]] = {}

# Variables read by the Flask app handlers
high_latency_seconds: float = 0.0      # extra sleep added to every request
error_rate_pct: float = 0.0            # 0‒100: % of requests that return 500
cpu_spike_active: bool = False         # background thread burns CPU
memory_leak_active: bool = False       # background thread grows a list
db_failure_active: bool = False        # DB calls return errors
service_degraded: bool = False         # general degraded flag (slower + errors)

_memory_sink = []                      # holds leaked memory objects
_background_threads: Dict[str, threading.Thread] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _register(name: str, details: Dict[str, Any]):
    with _lock:
        _active_incidents[name] = {
            "name": name,
            "started_at": _now(),
            **details,
        }


def _unregister(name: str):
    with _lock:
        _active_incidents.pop(name, None)


def _cpu_burn_loop():
    """Consume CPU until the cpu_spike_active flag is cleared."""
    global cpu_spike_active
    while cpu_spike_active:
        # Busy‑loop in short bursts so we can react to the flag quickly
        end = time.time() + 0.1
        while time.time() < end:
            _ = math.sqrt(987654321.123)
        time.sleep(0.005)


def _memory_grow_loop():
    """Append ~1 MB per second until the memory_leak_active flag is cleared."""
    global memory_leak_active, _memory_sink
    while memory_leak_active:
        _memory_sink.append(b"x" * 1024 * 1024)  # 1 MB
        time.sleep(1)


# ---------------------------------------------------------------------------
# Public API – called from the /incidents endpoints
# ---------------------------------------------------------------------------

def start_incident(incident_type: str, params: Optional[Dict] = None) -> Dict[str, Any]:
    """Activate an incident.  Returns a description dict or raises ValueError."""
    global high_latency_seconds, error_rate_pct, cpu_spike_active
    global memory_leak_active, db_failure_active, service_degraded

    params = params or {}

    if incident_type == "high_latency":
        seconds = float(params.get("seconds", 2.0))
        high_latency_seconds = seconds
        _register(incident_type, {"extra_latency_seconds": seconds})

    elif incident_type == "high_error_rate":
        pct = float(params.get("percentage", 50.0))
        if not 0 < pct <= 100:
            raise ValueError("percentage must be between 1 and 100")
        error_rate_pct = pct
        _register(incident_type, {"error_rate_percentage": pct})

    elif incident_type == "cpu_spike":
        if not cpu_spike_active:
            cpu_spike_active = True
            t = threading.Thread(target=_cpu_burn_loop, daemon=True, name="cpu-burn")
            t.start()
            _background_threads["cpu_spike"] = t
        _register(incident_type, {"description": "CPU burn loop running"})

    elif incident_type == "memory_leak":
        if not memory_leak_active:
            memory_leak_active = True
            t = threading.Thread(target=_memory_grow_loop, daemon=True, name="mem-grow")
            t.start()
            _background_threads["memory_leak"] = t
        _register(incident_type, {"description": "Memory growing ~1 MB/s"})

    elif incident_type == "db_failure":
        db_failure_active = True
        _register(incident_type, {"description": "All DB calls returning errors"})

    elif incident_type == "service_degraded":
        service_degraded = True
        # Moderate latency + moderate errors
        high_latency_seconds = max(high_latency_seconds, 1.0)
        error_rate_pct = max(error_rate_pct, 20.0)
        _register(incident_type, {"description": "Service in degraded mode (latency + errors)"})

    elif incident_type == "cascading_failure":
        # Everything at once
        db_failure_active = True
        cpu_spike_active = True
        high_latency_seconds = max(high_latency_seconds, 3.0)
        error_rate_pct = max(error_rate_pct, 70.0)
        service_degraded = True
        for name, target in [("cpu_spike", _cpu_burn_loop)]:
            if name not in _background_threads or not _background_threads[name].is_alive():
                t = threading.Thread(target=target, daemon=True, name=name)
                t.start()
                _background_threads[name] = t
        _register(incident_type, {"description": "Cascading failure: DB down, CPU spike, high latency, high errors"})

    else:
        raise ValueError(f"Unknown incident type: {incident_type!r}")

    return get_incident(incident_type)


def resolve_incident(incident_type: str) -> Dict[str, Any]:
    """Deactivate an incident. Returns what was resolved or raises ValueError."""
    global high_latency_seconds, error_rate_pct, cpu_spike_active
    global memory_leak_active, db_failure_active, service_degraded, _memory_sink

    with _lock:
        if incident_type not in _active_incidents:
            raise ValueError(f"No active incident: {incident_type!r}")

    if incident_type == "high_latency":
        high_latency_seconds = 0.0

    elif incident_type == "high_error_rate":
        error_rate_pct = 0.0

    elif incident_type == "cpu_spike":
        cpu_spike_active = False

    elif incident_type == "memory_leak":
        memory_leak_active = False
        _memory_sink = []
        gc.collect()

    elif incident_type == "db_failure":
        db_failure_active = False

    elif incident_type == "service_degraded":
        service_degraded = False
        high_latency_seconds = 0.0
        error_rate_pct = 0.0

    elif incident_type == "cascading_failure":
        cpu_spike_active = False
        db_failure_active = False
        high_latency_seconds = 0.0
        error_rate_pct = 0.0
        service_degraded = False

    resolved = _active_incidents.get(incident_type, {})
    _unregister(incident_type)
    return {"resolved": incident_type, "was": resolved}


def list_incidents() -> Dict[str, Any]:
    with _lock:
        return dict(_active_incidents)


def get_incident(name: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _active_incidents.get(name)


def resolve_all() -> Dict[str, Any]:
    """Resolve every active incident at once."""
    global high_latency_seconds, error_rate_pct, cpu_spike_active
    global memory_leak_active, db_failure_active, service_degraded, _memory_sink

    with _lock:
        resolved = list(_active_incidents.keys())
        _active_incidents.clear()

    cpu_spike_active = False
    memory_leak_active = False
    db_failure_active = False
    service_degraded = False
    high_latency_seconds = 0.0
    error_rate_pct = 0.0
    _memory_sink = []
    gc.collect()

    return {"resolved_all": resolved}
