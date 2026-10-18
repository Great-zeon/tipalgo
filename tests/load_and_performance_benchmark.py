"""
Independent Sustained Load & Latency Benchmark Script.

Measures direct sustained throughput (RPS), warm/cold cache latency percentiles (p50, p95, p99),
concurrent user load (10-20 workers), and prediction computation latency directly against
the local server and database without theoretical reciprocal extrapolation.
"""

import time
import concurrent.futures
import urllib.request
import urllib.error
import numpy as np
import sqlite3
import os
import sys

from main import bivariate_poisson_probs

SERVER_URL = "http://127.0.0.1:8000"
DB_PATH = "tipalgo_v21.db"

def run_benchmarks():
    print("=" * 70)
    print("INDEPENDENT PERFORMANCE, LATENCY & SUSTAINED THROUGHPUT BENCHMARK")
    print("=" * 70)
    
    # Check server availability
    try:
        req = urllib.request.Request(f"{SERVER_URL}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
        print("✓ Local backend server connected on port 8000\n")
    except Exception as e:
        print(f"✗ Failed to connect to backend on port 8000: {e}")
        sys.exit(1)

    # -------------------------------------------------------------
    # 1. Warm-Cache Latency (100 Requests)
    # -------------------------------------------------------------
    print("1. Measuring Warm-Cache Latency (100 requests to /api/calendar/daily-slate)...")
    # Pre-warm
    urllib.request.urlopen(f"{SERVER_URL}/api/calendar/daily-slate?date=2026-10-14&endpoint=all").read()
    
    warm_latencies = []
    for _ in range(100):
        t0 = time.perf_counter()
        with urllib.request.urlopen(f"{SERVER_URL}/api/calendar/daily-slate?date=2026-10-14&endpoint=all", timeout=5) as resp:
            resp.read()
        t1 = time.perf_counter()
        warm_latencies.append((t1 - t0) * 1000.0)

    p50_warm = float(np.percentile(warm_latencies, 50))
    p95_warm = float(np.percentile(warm_latencies, 95))
    p99_warm = float(np.percentile(warm_latencies, 99))
    print(f"   Warm Cache (100 reqs): p50 = {p50_warm:.2f} ms | p95 = {p95_warm:.2f} ms | p99 = {p99_warm:.2f} ms")

    # -------------------------------------------------------------
    # 2. Cold-Cache Latency (Distinct dates to force cache misses)
    # -------------------------------------------------------------
    print("\n2. Measuring Cold-Cache / Uncached Generation Latency (10 distinct dates)...")
    cold_latencies = []
    # Using far future / past dates to avoid pre-warmed cache
    test_dates = [f"2027-01-{day:02d}" for day in range(1, 11)]
    for d in test_dates:
        t0 = time.perf_counter()
        with urllib.request.urlopen(f"{SERVER_URL}/api/calendar/daily-slate?date={d}&endpoint=all", timeout=10) as resp:
            resp.read()
        t1 = time.perf_counter()
        cold_latencies.append((t1 - t0) * 1000.0)

    p50_cold = float(np.percentile(cold_latencies, 50))
    p95_cold = float(np.percentile(cold_latencies, 95))
    p99_cold = float(np.percentile(cold_latencies, 99))
    print(f"   Cold Cache (10 distinct dates): p50 = {p50_cold:.2f} ms | p95 = {p95_cold:.2f} ms | p99 = {p99_cold:.2f} ms")

    # -------------------------------------------------------------
    # 3. Direct Uncached Database SQLite Query Latency
    # -------------------------------------------------------------
    print("\n3. Measuring Direct SQLite Query Latency (100 queries)...")
    db_latencies = []
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        for _ in range(100):
            t0 = time.perf_counter()
            cursor.execute("SELECT id, home_team, away_team, match_date, home_odds FROM historical_matches LIMIT 20")
            cursor.fetchall()
            t1 = time.perf_counter()
            db_latencies.append((t1 - t0) * 1000.0)
        conn.close()
        p50_db = float(np.percentile(db_latencies, 50))
        p95_db = float(np.percentile(db_latencies, 95))
        p99_db = float(np.percentile(db_latencies, 99))
        print(f"   Direct SQLite (WAL mode): p50 = {p50_db:.3f} ms | p95 = {p95_db:.3f} ms | p99 = {p99_db:.3f} ms")
    else:
        print("   Database file not found at default path, skipping direct query.")

    # -------------------------------------------------------------
    # 4. Pure Mathematical Computation Latency (1,000 iterations)
    # -------------------------------------------------------------
    print("\n4. Measuring Pure Bivariate Poisson Math Computation (1,000 runs)...")
    math_latencies = []
    for _ in range(1000):
        t0 = time.perf_counter()
        bivariate_poisson_probs(1.75, 1.15, 0.07, max_goals=10)
        t1 = time.perf_counter()
        math_latencies.append((t1 - t0) * 1000.0)

    p50_math = float(np.percentile(math_latencies, 50))
    p95_math = float(np.percentile(math_latencies, 95))
    p99_math = float(np.percentile(math_latencies, 99))
    print(f"   Bivariate Poisson Math: p50 = {p50_math:.3f} ms | p95 = {p95_math:.3f} ms | p99 = {p99_math:.3f} ms")

    # -------------------------------------------------------------
    # 5. Direct Sustained Throughput Under Concurrency (5-Second Load Test)
    # -------------------------------------------------------------
    print("\n5. Measuring Directly Measured Sustained Throughput (5-second sustained load)...")
    concurrency_levels = [5, 10, 20]
    duration_secs = 5.0

    for c in concurrency_levels:
        completed = 0
        errors = 0
        stop_time = time.time() + duration_secs

        def worker():
            nonlocal completed, errors
            local_completed = 0
            local_errors = 0
            while time.time() < stop_time:
                try:
                    with urllib.request.urlopen(f"{SERVER_URL}/api/calendar/daily-slate?date=2026-10-14&endpoint=all", timeout=3) as resp:
                        resp.read()
                        local_completed += 1
                except Exception:
                    local_errors += 1
            return local_completed, local_errors

        t_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=c) as executor:
            futures = [executor.submit(worker) for _ in range(c)]
            for fut in concurrent.futures.as_completed(futures):
                c_ok, c_err = fut.result()
                completed += c_ok
                errors += c_err
        t_end = time.perf_counter()

        actual_duration = t_end - t_start
        actual_rps = completed / actual_duration
        error_rate = (errors / (completed + errors)) * 100 if (completed + errors) > 0 else 0.0

        print(f"   Concurrency = {c:2d} workers | Completed = {completed:4d} reqs | Measured RPS = {actual_rps:6.1f} req/s | Errors = {errors} ({error_rate:.1f}%)")

    print("\n" + "=" * 70)
    print("BENCHMARK COMPLETED SUCCESSFULLY")
    print("=" * 70)

if __name__ == "__main__":
    run_benchmarks()
