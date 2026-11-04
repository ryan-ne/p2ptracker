import asyncio
import base64
import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app import users as users_cli
from app.config import Settings, get_settings
from app.main import create_app


def test_password_hashing():
    h = auth.hash_password("correct horse")
    assert h.startswith("scrypt$") and "correct horse" not in h
    assert auth.check_password("correct horse", h)
    assert not auth.check_password("wrong", h)
    assert not auth.check_password("x", "garbage")
    assert auth.hash_password("same") != auth.hash_password("same")      # salted


def make(tmp_path, env_user="", env_pass=""):
    s = Settings(db_path=str(tmp_path / "u.db"), dashboard_user=env_user, dashboard_pass=env_pass, _env_file=None)
    return create_app(s, start_background=False)


def add_user(c, name, pw):
    c.portal.call(c.app.state.db.add_user, name, auth.hash_password(pw), int(time.time()))


def login(c, user, pw):
    return c.post("/api/login", json={"username": user, "password": pw})


def test_no_login_until_first_user_added(tmp_path):
    with TestClient(make(tmp_path), follow_redirects=False) as c:
        assert c.get("/api/alerts").status_code == 200          # open: no .env login, no users
        add_user(c, "alice", "alice-pass-1")
        assert c.get("/api/alerts").status_code == 401          # first user turns login on immediately
        assert c.get("/").status_code == 303


def test_multiple_users_sign_in_independently(tmp_path):
    with TestClient(make(tmp_path)) as a, TestClient(a.app) as b:
        add_user(a, "alice", "alice-pass-1")
        add_user(a, "bob", "bob-pass-12")
        assert login(a, "Alice", "alice-pass-1").json() == {"ok": True, "user": "alice"}   # case-insensitive
        assert login(b, "bob", "bob-pass-12").status_code == 200
        assert a.get("/api/me").json() == {"auth": True, "user": "alice"}
        assert b.get("/api/me").json() == {"auth": True, "user": "bob"}
        assert login(a, "bob", "alice-pass-1").status_code == 401            # wrong password
        assert login(a, "nobody", "whatever-123").status_code == 401         # unknown user
        assert a.get("/api/alerts").status_code == 200                       # failed attempts don't log out

        # password change signs out only that user
        a.portal.call(a.app.state.db.set_user_password, "alice", auth.hash_password("new-alice-pass"))
        assert a.get("/api/alerts").status_code == 401
        assert b.get("/api/alerts").status_code == 200
        assert login(a, "alice", "new-alice-pass").status_code == 200

        # removal signs the user out
        a.portal.call(a.app.state.db.delete_user, "bob")
        assert b.get("/api/alerts").status_code == 401
        assert login(b, "bob", "bob-pass-12").status_code == 401


def test_env_account_and_db_users_coexist(tmp_path):
    with TestClient(make(tmp_path, "ryan", "env-pass-123")) as c:
        add_user(c, "alice", "alice-pass-1")
        assert login(c, "ryan", "env-pass-123").json()["user"] == "ryan"
        assert c.get("/api/me").json()["user"] == "ryan"
        assert login(c, "alice", "alice-pass-1").json()["user"] == "alice"
        # session cookie format from before multi-user still validates for the .env account
        secret = c.app.state.session_secret
        c.cookies.clear()
        c.cookies.set("p2p_session", auth.make_token("ryan", f"{secret}:env-pass-123"))
        assert c.get("/api/me").json()["user"] == "ryan"


def test_basic_auth_for_db_user_scripts_only(tmp_path):
    with TestClient(make(tmp_path)) as c:
        add_user(c, "alice", "alice-pass-1")
        hdr = {"Authorization": "Basic " + base64.b64encode(b"alice:alice-pass-1").decode()}
        assert c.get("/api/alerts", headers=hdr).status_code == 200
        assert c.get("/api/alerts", headers={**hdr, "Sec-Fetch-Site": "same-origin"}).status_code == 401


# ---------- server command ----------
@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("DASHBOARD_USER", "ryan")
    monkeypatch.setenv("DASHBOARD_PASS", "env-pass-123")
    monkeypatch.chdir(tmp_path)              # no .env file here
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def cli(*argv):
    return asyncio.run(users_cli.main(list(argv)))


def test_cli_add_list_passwd_remove(cli_env, capsys):
    assert cli("add", "Alice", "--generate") == 0
    out = capsys.readouterr().out
    assert "Added 'alice'" in out and "Password: " in out
    generated = out.split("Password: ")[1].strip()
    assert len(generated) >= auth.MIN_PASSWORD

    cli("list")
    out = capsys.readouterr().out
    assert "ryan" in out and "(from .env)" in out and "alice" in out

    with pytest.raises(SystemExit, match="already exists"):
        cli("add", "alice", "--generate")
    with pytest.raises(SystemExit, match=".env account"):
        cli("add", "RYAN", "--generate")
    with pytest.raises(SystemExit, match="Username"):
        cli("add", "bad name!", "--generate")

    assert cli("passwd", "alice", "--generate") == 0
    assert "old sessions signed out" in capsys.readouterr().out
    assert cli("remove", "alice") == 0
    with pytest.raises(SystemExit, match="No user"):
        cli("remove", "alice")


def test_cli_wont_remove_last_account_when_no_env_login(cli_env, monkeypatch, capsys):
    monkeypatch.delenv("DASHBOARD_USER")
    monkeypatch.delenv("DASHBOARD_PASS")
    get_settings.cache_clear()
    cli("add", "alice", "--generate")
    with pytest.raises(SystemExit, match="only account"):
        cli("remove", "alice")
    cli("add", "bob", "--generate")
    assert cli("remove", "alice") == 0          # fine once another account exists


def test_cli_refuses_to_prompt_without_terminal(cli_env, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit, match="--generate"):
        cli("add", "dave")
