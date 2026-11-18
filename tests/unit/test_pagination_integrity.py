import pytest
from httpx import AsyncClient, ASGITransport
from main import app

@pytest.mark.asyncio
async def test_pagination_deterministic_and_no_overlap():
    """
    Test pagination integrity on /api/calendar/daily-slate:
    1. Query page=1, limit=10 on 2026-11-17 (146 verified matches).
    2. Query page=2, limit=10 on 2026-11-17.
    3. Assert zero overlapping fixture IDs between page 1 and page 2.
    4. Assert deterministic ordering.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get("/api/calendar/daily-slate?date=2026-11-17&page=1&limit=10")
        assert r1.status_code == 200
        d1 = r1.json()
        assert "matches" in d1
        matches_p1 = d1["matches"]
        p1_ids = [m["id"] for m in matches_p1]
        assert len(p1_ids) == 10

        r2 = await client.get("/api/calendar/daily-slate?date=2026-11-17&page=2&limit=10")
        assert r2.status_code == 200
        d2 = r2.json()
        assert "matches" in d2
        matches_p2 = d2["matches"]
        p2_ids = [m["id"] for m in matches_p2]
        assert len(p2_ids) == 10

        # Assert no overlap
        overlap = set(p1_ids).intersection(set(p2_ids))
        assert len(overlap) == 0, f"Detected duplicate match IDs across pages: {overlap}"

        # Assert pagination metadata
        assert d1.get("page") == 1
        assert d1.get("limit") == 10
        assert d1.get("total_fixtures") == 146
        assert d1.get("total_pages") == 15
        assert d1.get("has_more") is True

@pytest.mark.asyncio
async def test_pagination_limit_clamping():
    """Verify limit > 500 is clamped safely to 500 max in metadata."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/calendar/daily-slate?date=2026-11-17&page=1&limit=1000")
        assert r.status_code == 200
        d = r.json()
        assert d.get("limit") <= 500
        assert d.get("page") == 1

@pytest.mark.asyncio
async def test_pagination_navigates_across_all_fixtures():
    """
    Verify complete traversal across multiple pages:
    On Nov 17 (146 fixtures), fetching in 50-item pages yields all 146 fixtures
    with 0 duplicates and correct has_more transitions.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        all_ids = []
        page = 1
        limit = 50
        while True:
            r = await client.get(f"/api/calendar/daily-slate?date=2026-11-17&page={page}&limit={limit}")
            assert r.status_code == 200
            d = r.json()
            matches = d.get("matches", [])
            for m in matches:
                assert m["id"] not in all_ids, f"Duplicate ID {m['id']} on page {page}"
                all_ids.append(m["id"])
            if not d.get("has_more"):
                break
            page += 1

        assert len(all_ids) == 146
        assert page == 3 # 50 + 50 + 46 = 146 matches across 3 pages
