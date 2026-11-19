import sys
import types
from unittest.mock import MagicMock, patch

from keyring_linux_bootstrap import bootstrap_linux_keyring


def test_bootstrap_does_nothing_when_env_var_unset():
    # No KEYRING_CRYPTFILE_PASSWORD -- macOS/Windows (and a normal Linux desktop with a
    # working Secret Service) must be completely unaffected.
    with patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("KEYRING_CRYPTFILE_PASSWORD", None)
        with patch("keyring_linux_bootstrap.keyring.set_keyring") as mock_set_keyring:
            bootstrap_linux_keyring()
    mock_set_keyring.assert_not_called()


def test_bootstrap_switches_to_cryptfile_keyring_when_env_var_set():
    # keyrings.cryptfile isn't installed on every platform (Linux-only requirements.txt
    # marker) -- stub the module so this test doesn't need it actually installed.
    fake_backend = MagicMock()
    fake_module = types.ModuleType("keyrings.cryptfile.cryptfile")
    fake_module.CryptFileKeyring = MagicMock(return_value=fake_backend)

    with patch.dict("os.environ", {"KEYRING_CRYPTFILE_PASSWORD": "super-secret-master-pw"}):
        with patch.dict(sys.modules, {"keyrings.cryptfile.cryptfile": fake_module}):
            with patch("keyring_linux_bootstrap.keyring.set_keyring") as mock_set_keyring:
                bootstrap_linux_keyring()

    assert fake_backend.keyring_key == "super-secret-master-pw"
    mock_set_keyring.assert_called_once_with(fake_backend)
