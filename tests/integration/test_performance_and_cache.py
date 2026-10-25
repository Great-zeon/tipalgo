import time
import asyncio
import numpy as np
import pytest
from starlette.testclient import TestClient
from main import app, calendar_slate_cache, bivariate_poisson_probs

def test_cache_and_performance_benchmarks():
    client = TestClient(app)
    
    # 1. Warm-up request
    client.get("/api/calendar/daily-slate?date=2026-10-14")
    
    # 2. Warm cache benchmark (50 iterations)
    warm_latencies = []
    for _ in range(50):
        t0 = time.perf_counter()
        res = client.get("/api/calendar/daily-slate?date=2026-10-14")
        t1 = time.perf_counter()
        assert res.status_code == 200
        warm_latencies.append((t1 - t0) * 1000.0) # ms
    
    p50_warm = float(np.percentile(warm_latencies, 50))
    p95_warm = float(np.percentile(warm_latencies, 95))
    p99_warm = float(np.percentile(warm_latencies, 99))
    print(f"\nWarm Cache Latency (50 reqs): p50={p50_warm:.2f}ms, p95={p95_warm:.2f}ms, p99={p99_warm:.2f}ms")
    
    # Assert warm cache is sub-35ms
    assert p50_warm < 35.0, f"Warm cache p50 too high: {p50_warm}ms"

    # 3. Prediction computation benchmark (Bivariate Poisson 100 iterations)
    math_latencies = []
    for _ in range(100):
        t0 = time.perf_counter()
        bivariate_poisson_probs(1.8, 1.2, 0.08, max_goals=10)
        t1 = time.perf_counter()
        math_latencies.append((t1 - t0) * 1000.0)
    
    p50_math = float(np.percentile(math_latencies, 50))
    print(f"Bivariate Poisson Calculation Latency: p50={p50_math:.3f}ms")
    assert p50_math < 2.0, f"Poisson math calculation too slow: {p50_math}ms"

def test_concurrent_requests_no_cross_contamination():
    """Verify concurrent requests for distinct dates/endpoints return isolated, correct data."""
    client = TestClient(app)
    dates = ["2026-10-13", "2026-10-14", "2026-10-15", "next-5-days"]
    endpoints = ["all", "home-win", "over-2-5", "btts-yes"]
    
    import concurrent.futures
    
    def fetch_slate(d, ep):
        res = client.get(f"/api/calendar/daily-slate?date={d}&endpoint={ep}")
        return d, ep, res.status_code, res.json()
    
    tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for d in dates:
            for ep in endpoints:
                tasks.append(executor.submit(fetch_slate, d, ep))
                
        for fut in concurrent.futures.as_completed(tasks):
            req_date, req_ep, status, data = fut.result()
            assert status == 200
            assert data["selected_date"] == req_date, f"Cross-contamination: Expected {req_date}, got {data['selected_date']}"
            assert data["active_endpoint"] == req_ep, f"Cross-contamination: Expected {req_ep}, got {data['active_endpoint']}"
