#!/usr/bin/env python3
"""Manage the InfoMentor username/password used for automated (non-BankID) login.

The password is entered once, interactively, and stored exclusively in the OS keychain
via the `keyring` package. It is never written to a file, environment variable, or CLI
argument, and infomentor_extract.py only ever reads it back from the keychain at run
time. See CLAUDE.md principle 3 for the full set of constraints this must satisfy.

BankID/SSO login is unaffected by this and remains fully manual.
"""

import argparse
import getpass

import keyring
import keyring.errors

from infomentor_extract import CREDENTIAL_SERVICE_NAME
from keyring_linux_bootstrap import bootstrap_linux_keyring
from local_config import save_config

bootstrap_linux_keyring()


def set_credentials(username: str) -> None:
    password = getpass.getpass(f"InfoMentor password for {username}: ")
    if not password:
        raise SystemExit("Refusing to store an empty password.")
    keyring.set_password(CREDENTIAL_SERVICE_NAME, username, password)
    save_config({"infomentor_username": username})
    print(f"Stored credentials for {username!r} in the OS keychain.")
    print(f"Saved {username!r} as the default username, so --username can be omitted from now on.")


def clear_credentials(username: str) -> None:
    try:
        keyring.delete_password(CREDENTIAL_SERVICE_NAME, username)
        print(f"Removed stored credentials for {username!r}.")
    except keyring.errors.PasswordDeleteError:
        print(f"No stored credentials found for {username!r}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage InfoMentor login credentials (OS keychain only)")
    sub = parser.add_subparsers(dest="command", required=True)

    set_p = sub.add_parser("set", help="Interactively store a username/password in the OS keychain")
    set_p.add_argument("--username", required=True)

    clear_p = sub.add_parser("clear", help="Remove a stored credential from the OS keychain")
    clear_p.add_argument("--username", required=True)

    args = parser.parse_args()
    if args.command == "set":
        set_credentials(args.username)
    elif args.command == "clear":
        clear_credentials(args.username)


if __name__ == "__main__":
    main()
