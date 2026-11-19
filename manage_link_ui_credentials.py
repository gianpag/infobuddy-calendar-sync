#!/usr/bin/env python3
"""Set the login for calendar_link_ui.py (the Google Calendar accounts UI on :8200).

    python manage_link_ui_credentials.py --username <you>                     # prompts
    python manage_link_ui_credentials.py --username <you> --password-stdin <<<"$PW"

Stored only in the OS keyring (service `infobuddy-link-ui`; see link_ui_auth.py). On a
headless Linux host, load KEYRING_CRYPTFILE_PASSWORD first (same as the services do; see
keyring_linux_bootstrap.py), or the entry lands in a different keyring backend.
Restart the link UI afterwards (`inkbuddy restart link`).
"""
from __future__ import annotations

import argparse
import getpass
import sys

import link_ui_auth
from keyring_linux_bootstrap import bootstrap_linux_keyring

bootstrap_linux_keyring()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--username", required=True)
    p.add_argument("--password-stdin", action="store_true", help="read the password from the first line of stdin")
    args = p.parse_args()
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass(f"Password for {args.username!r}: ")
        if password != getpass.getpass("Confirm password: "):
            raise SystemExit("Passwords did not match; nothing was stored.")
    if not password:
        raise SystemExit("Refusing to store an empty password.")
    link_ui_auth.set_credentials(args.username, password)
    print(f"Stored link-UI login for {args.username!r} in the keyring (service {link_ui_auth.LINK_UI_SERVICE_NAME!r}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
