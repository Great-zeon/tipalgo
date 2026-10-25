import pytest
from starlette.testclient import TestClient
from main import app, config

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c

def test_device_binding_and_lockout(client):
    # 1. Reset device table via override endpoint
    reset_res = client.post("/auth/device-override", json={
        "override_pin": config.ADMIN_DEVICE_KEY,
        "device_id": "test_reset",
        "action": "reset_all"
    })
    assert reset_res.status_code == 200

    tokens = []
    # 2. Register devices up to MAX_SYSTEM_DEVICES (e.g. 5 devices across partners and users)
    max_devs = config.MAX_SYSTEM_DEVICES
    for i in range(1, max_devs + 1):
        username = "tip_algo" if i % 2 == 1 else "tip_partner"
        res = client.post("/auth/login", json={
            "username": username,
            "password": config.TIPALGO_MASTER_PASSWORD,
            "device_id": f"device_authorized_{i}",
            "device_name": f"Hardware Unit {i}"
        })
        assert res.status_code == 200, f"Device {i} failed to register: {res.text}"
        data = res.json()
        assert data["expires_in_days"] == 30
        tokens.append(data["access_token"])

    # 3. Attempt login with Device (N + 1) (Exceeding max capacity) -> MUST BE BLOCKED
    blocked_res = client.post("/auth/login", json={
        "username": "tip_algo",
        "password": config.TIPALGO_MASTER_PASSWORD,
        "device_id": f"device_unauthorized_{max_devs + 1}",
        "device_name": "Unauthorized Intruder Device"
    })
    assert blocked_res.status_code == 403
    err_detail = blocked_res.json()["detail"]
    assert err_detail["error_code"] == "DEVICE_LIMIT_REACHED"
    assert len(err_detail["active_devices"]) == max_devs

    # 4. Master PIN Override: Legitimate owner authorizes device with PIN
    override_res = client.post("/auth/login", json={
        "username": "tip_algo",
        "password": config.TIPALGO_MASTER_PASSWORD,
        "device_id": f"device_unauthorized_{max_devs + 1}",
        "device_name": "New Replacement Hardware",
        "override_pin": config.ADMIN_DEVICE_KEY
    })
    assert override_res.status_code == 200

    # 5. Verify session endpoint works
    verify_res = client.get("/auth/verify-session", headers={"Authorization": f"Bearer {tokens[0]}"})
    assert verify_res.status_code == 200
    assert verify_res.json()["valid"] is True

    # 6. Check active devices list
    devices_res = client.get("/auth/devices", headers={"Authorization": f"Bearer {tokens[0]}"})
    assert devices_res.status_code == 200
    assert devices_res.json()["total_active"] == max_devs
