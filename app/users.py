"""Manage dashboard users from the server.

    docker compose exec tracker python -m app.users add alice        # asks for a password
    docker compose exec tracker python -m app.users add bob --generate  # prints a random one
    docker compose exec tracker python -m app.users list
    docker compose exec tracker python -m app.users passwd alice
    docker compose exec tracker python -m app.users remove alice

Changes apply immediately to the running app (no restart). Changing a password or removing a
user signs that user out everywhere. The DASHBOARD_USER/DASHBOARD_PASS account from .env keeps
working alongside these users.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys
import time
from datetime import datetime

from . import auth
from .config import get_settings
from .db import Database


def _ask_password(args) -> tuple[str, bool]:
    """Returns (password, generated)."""
    if args.generate:
        return secrets.token_urlsafe(12), True
    if not sys.stdin.isatty():
        sys.exit("No terminal to type a password: use --generate, or run with `docker compose exec -it`.")
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords don't match.")
    return pw, False


def _check(pw: str) -> None:
    if len(pw) < auth.MIN_PASSWORD:
        sys.exit(f"Password must be at least {auth.MIN_PASSWORD} characters.")


async def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.users", description="Manage dashboard users.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("add", "add a user"), ("passwd", "change a user's password")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("username")
        sp.add_argument("--generate", action="store_true", help="generate a random password and print it")
    sub.add_parser("list", help="list users")
    rm = sub.add_parser("remove", help="remove a user (signs them out)")
    rm.add_argument("username")
    args = p.parse_args(argv)

    s = get_settings()
    db = Database(s.db_path)
    await db.connect()
    try:
        if args.cmd == "list":
            users = await db.list_users()
            if s.dashboard_user and s.dashboard_pass:
                print(f"{s.dashboard_user:<24} (from .env)")
            for u in users:
                created = datetime.fromtimestamp(u["created_at"]).strftime("%Y-%m-%d %H:%M")
                print(f"{u['username']:<24} added {created}")
            if not users and not (s.dashboard_user and s.dashboard_pass):
                print("No users - the dashboard has no login. Add one with: python -m app.users add <name>")
            return 0

        name = args.username.strip().lower()
        if not auth.USERNAME_RE.match(name):
            sys.exit("Username: 2-32 characters, letters, digits, dot, dash or underscore.")
        if s.dashboard_user and name == s.dashboard_user.lower():
            sys.exit(f"'{name}' is the .env account; change it in .env instead.")

        if args.cmd == "remove":
            env_account = bool(s.dashboard_user and s.dashboard_pass)
            if not env_account and await db.get_user(name) and await db.count_users() == 1:
                sys.exit(f"'{name}' is the only account: removing it would leave the dashboard open to "
                         "anyone. Add another user first (or set DASHBOARD_USER/PASS in .env).")
            if not await db.delete_user(name):
                sys.exit(f"No user '{name}'.")
            print(f"Removed '{name}' (signed out everywhere).")
            return 0

        exists = await db.get_user(name) is not None
        if args.cmd == "add" and exists:
            sys.exit(f"User '{name}' already exists (use passwd to change the password).")
        if args.cmd == "passwd" and not exists:
            sys.exit(f"No user '{name}'.")
        pw, generated = _ask_password(args)
        _check(pw)
        h = auth.hash_password(pw)
        if args.cmd == "add":
            await db.add_user(name, h, int(time.time()))
            print(f"Added '{name}'.")
        else:
            await db.set_user_password(name, h)
            print(f"Password changed for '{name}' (old sessions signed out).")
        if generated:
            print(f"Password: {pw}")
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
