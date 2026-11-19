"""Linux-only keyring fallback for hosts with no Secret Service provider.

The documented Linux setup (scheduling/linux/, gnome-keyring + PAM) assumes a password
login happens at least once to trigger PAM's auth-stage keyring unlock. A host accessed
purely via SSH keys (verified against a real VM: SSH pubkey auth never invokes that PAM
stage) never triggers it, so `keyring` has nowhere to store/read secrets at all --
`keyring.get_password()`/`set_password()` just raise. CLAUDE.md principle 2/3 require the
OS keychain, never a plaintext file, so the fallback here is an AES-encrypted file
keyring (`keyrings.cryptfile`), not a plaintext one.

bootstrap_linux_keyring() only engages when KEYRING_CRYPTFILE_PASSWORD is set in the
environment -- macOS and Windows never set it and keep using their native OS keychain
untouched. That master password has to live somewhere a non-interactive service can read
it at startup: supply it via systemd's `EnvironmentFile=` pointing at a 0600 file only
the service's own user can read (see scheduling/linux/*.service.template). This is a
narrower guarantee than macOS/Windows' native keychains -- a deliberate, scoped trade-off
for headless/SSH-key-only Linux hosts specifically, not a general change to how
credentials are stored elsewhere.
"""
from __future__ import annotations

import os

import keyring


def bootstrap_linux_keyring() -> None:
    master_password = os.environ.get("KEYRING_CRYPTFILE_PASSWORD")
    if not master_password:
        return
    from keyrings.cryptfile.cryptfile import CryptFileKeyring

    backend = CryptFileKeyring()
    backend.keyring_key = master_password
    keyring.set_keyring(backend)
