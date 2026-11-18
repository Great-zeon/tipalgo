import pytest
import importlib
from unittest.mock import patch
from main import verify_password, hash_password
import main

def test_production_master_password_bypass_disabled():
    with patch.dict("os.environ", {"ENVIRONMENT": "production", "TIPALGO_MASTER_PASSWORD": "SuperSecretMasterKey12345!"}):
        importlib.reload(main)
        prod_config = main.config
        assert prod_config._is_prod is True
        assert prod_config.TIPALGO_MASTER_PASSWORD is None
        assert prod_config.MASTER_PASSWORD_BYPASS_ENABLED is False
    # Reload back to dev
    with patch.dict("os.environ", {"ENVIRONMENT": "development"}):
        importlib.reload(main)

def test_dev_master_password_bypass_controlled():
    with patch.dict("os.environ", {"ENVIRONMENT": "development", "TIPALGO_MASTER_PASSWORD": "DevPassword123!"}):
        importlib.reload(main)
        dev_config = main.config
        assert dev_config._is_prod is False
        assert dev_config.TIPALGO_MASTER_PASSWORD == "DevPassword123!"
        assert dev_config.MASTER_PASSWORD_BYPASS_ENABLED is True
    # Reload back to default
    importlib.reload(main)

def test_unique_account_password_hashes():
    h1 = hash_password("PasswordForTipAlgo!@#")
    h2 = hash_password("PasswordForTipPartner!@#")
    assert h1 != h2
    assert verify_password("PasswordForTipAlgo!@#", h1) is True
    assert verify_password("PasswordForTipPartner!@#", h1) is False
    assert verify_password("PasswordForTipPartner!@#", h2) is True
    assert verify_password("TipAlgo2026!", h1) is False
    assert verify_password("TipAlgo2026!", h2) is False
