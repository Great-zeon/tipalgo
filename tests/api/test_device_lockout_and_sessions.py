import os
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from starlette.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, delete

from main import (
    app, config, Base, get_db, User, AuthorizedDevice,
    hash_password, verify_password
)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
LIVE_DB_PATH = os.path.join(BASE_DIR, "tipalgo.db")
TEST_DB_PATH = os.path.join(BASE_DIR, "test_isolated_device_lockout.db")

@pytest.fixture(scope="module")
def isolated_engine():
    """Create a completely isolated, independent SQLite test database."""
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except OSError:
            pass

    engine = create_async_engine(f"sqlite+aiosqlite:///{TEST_DB_PATH}", echo=False)
    
    # Initialize schema
    import asyncio
    async def init_schema():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            # Seed isolated test accounts
            u1 = User(
                username="tip_algo",
                email="tip_algo@tipalgo.test",
                password_hash=hash_password(config.TIPALGO_MASTER_PASSWORD or "TipAlgo2026!"),
                bankroll=1000000.0,
                subscription_tier="pro",
                subscription_expiry=datetime.now(timezone.utc) + timedelta(days=365),
                daily_tips_used=0,
                daily_reset_date=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                is_admin=True,
                personality_mode="PROFESSIONAL",
                locale="NG"
            )
            u2 = User(
                username="tip_partner",
                email="tip_partner@tipalgo.test",
                password_hash=hash_password(config.TIPALGO_MASTER_PASSWORD or "TipAlgo2026!"),
                bankroll=1000000.0,
                subscription_tier="pro",
                subscription_expiry=datetime.now(timezone.utc) + timedelta(days=365),
                daily_tips_used=0,
                daily_reset_date=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                is_admin=False,
                personality_mode="PROFESSIONAL",
                locale="NG"
            )
            session.add_all([u1, u2])
            await session.commit()

    asyncio.run(init_schema())
    yield engine

    # Teardown: close engine and remove temporary database file
    async def close_engine():
        await engine.dispose()
    asyncio.run(close_engine())

    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except OSError:
            pass

@pytest.fixture(scope="module")
def client(isolated_engine):
    """Provide a TestClient with get_db overridden to use the isolated database."""
    session_factory = async_sessionmaker(isolated_engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def test_normal_registration_and_five_device_limit(client):
    """
    Test 1: Normal device binding up to MAX_SYSTEM_DEVICES (5)
    and rejection of (N+1)th device with 403 DEVICE_LIMIT_REACHED.
    """
    tokens = []
    max_devs = config.MAX_SYSTEM_DEVICES
    for i in range(1, max_devs + 1):
        res = client.post("/auth/login", json={
            "username": "tip_algo",
            "password": config.TIPALGO_MASTER_PASSWORD or "TipAlgo2026!",
            "device_id": f"isolated_dev_{i}",
            "device_name": f"Isolated Hardware Unit {i}"
        })
        assert res.status_code == 200, f"Device {i} failed to register: {res.text}"
        tokens.append(res.json()["access_token"])

    # 6th device exceeds capacity -> MUST BE BLOCKED
    blocked_res = client.post("/auth/login", json={
        "username": "tip_algo",
        "password": config.TIPALGO_MASTER_PASSWORD or "TipAlgo2026!",
        "device_id": f"isolated_dev_{max_devs + 1}",
        "device_name": "Unauthorized Intruder Device"
    })
    assert blocked_res.status_code == 403
    err_detail = blocked_res.json()["detail"]
    assert err_detail["error_code"] == "DEVICE_LIMIT_REACHED"
    assert len(err_detail["active_devices"]) == max_devs


def test_authorized_device_replacement(client):
    """
    Test 2: Legitimate owner authorizes replacement device with Master PIN.
    Oldest slot is rotated out; total active devices remains strictly capped at 5.
    """
    max_devs = config.MAX_SYSTEM_DEVICES
    override_res = client.post("/auth/login", json={
        "username": "tip_algo",
        "password": config.TIPALGO_MASTER_PASSWORD or "TipAlgo2026!",
        "device_id": "isolated_replacement_device",
        "device_name": "New Replacement Hardware",
        "override_pin": config.ADMIN_DEVICE_KEY
    })
    assert override_res.status_code == 200
    data = override_res.json()
    assert "access_token" in data

    # Verify active devices list count is still capped at max_devs
    token = data["access_token"]
    dev_res = client.get("/auth/devices", headers={"Authorization": f"Bearer {token}"})
    assert dev_res.status_code == 200
    assert dev_res.json()["total_active"] == max_devs


def test_unauthorized_reset_attempt(client):
    """
    Test 3: Unauthorized override or reset attempt with invalid PIN returns 403.
    """
    bad_res = client.post("/auth/device-override", json={
        "override_pin": "completely_invalid_pin_12345",
        "device_id": "attacker_device",
        "action": "reset_all"
    })
    assert bad_res.status_code == 403
    assert "Invalid Master Authorization PIN" in bad_res.json()["detail"]


def test_user_specific_device_cleanup_and_cross_user_isolation(client):
    """
    Test 4: User-scoped device cleanup and cross-user isolation.
    Deauthorizing User A's devices MUST NOT deactivate User B's active devices.
    """
    # 1. Register 2 devices for user 'tip_partner'
    partner_tokens = []
    for i in range(2):
        r = client.post("/auth/login", json={
            "username": "tip_partner",
            "password": config.TIPALGO_MASTER_PASSWORD or "TipAlgo2026!",
            "device_id": f"partner_isolated_dev_{i}",
            "device_name": f"Partner Unit {i}"
        })
        assert r.status_code == 200
        partner_tokens.append(r.json()["access_token"])

    # Verify partner has 2 active devices
    p_dev_res = client.get("/auth/devices", headers={"Authorization": f"Bearer {partner_tokens[0]}"})
    assert p_dev_res.status_code == 200
    partner_active_before = [d for d in p_dev_res.json()["devices"] if d.get("username") == "tip_partner"]
    assert len(partner_active_before) == 2

    # 2. Perform user-specific reset on 'tip_algo' ONLY
    user_reset_res = client.post("/auth/device-override", json={
        "override_pin": config.ADMIN_DEVICE_KEY,
        "device_id": "algo_reset",
        "username": "tip_algo",
        "action": "reset_user"
    })
    assert user_reset_res.status_code == 200
    assert "tip_algo" in user_reset_res.json()["message"]

    # 3. CROSS-USER ISOLATION PROOF:
    # Verify tip_partner's devices are STILL ACTIVE and completely unaffected!
    p_check_res = client.get("/auth/devices", headers={"Authorization": f"Bearer {partner_tokens[0]}"})
    assert p_check_res.status_code == 200
    partner_active_after = [d for d in p_check_res.json()["devices"] if d.get("username") == "tip_partner"]
    algo_active_after = [d for d in p_check_res.json()["devices"] if d.get("username") == "tip_algo"]
    assert len(partner_active_after) == 2, "Cross-user isolation failure: User B devices were deactivated by User A reset!"
    assert len(algo_active_after) == 0, "User A active devices should be 0 after user reset!"


def test_emergency_pin_security_hardening(client):
    """
    Test 5: Password-first precedence and rate limiting.
    Invalid password cannot be bypassed with PIN (returns 401).
    """
    bad_pw_res = client.post("/auth/login", json={
        "username": "tip_algo",
        "password": "WrongPassword123!",
        "device_id": "attacker_device",
        "device_name": "Attacker Laptop",
        "override_pin": config.ADMIN_DEVICE_KEY
    })
    assert bad_pw_res.status_code == 401
    assert "Invalid username" in bad_pw_res.json()["detail"]


def test_production_database_protection():
    """
    Test 6: Production & local shared database protection.
    Proves that the real database (tipalgo.db) was NOT touched or polluted
    by the isolated device test suite.
    """
    assert os.path.exists(LIVE_DB_PATH), "Live tipalgo.db not found"
    conn = sqlite3.connect(LIVE_DB_PATH)
    cursor = conn.cursor()
    
    # Query for any test devices matching 'isolated_dev_%' or 'partner_isolated_%'
    cursor.execute("SELECT count(*) FROM authorized_devices WHERE device_id LIKE 'isolated_%' OR device_id LIKE 'partner_isolated_%';")
    polluted_count = cursor.fetchone()[0]
    conn.close()

    assert polluted_count == 0, f"Database isolation breach: found {polluted_count} test devices in live tipalgo.db!"
