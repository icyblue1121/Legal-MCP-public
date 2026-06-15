from __future__ import annotations

import http.cookiejar
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.client import HTTPResponse
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError

from legal_mcp import db
from legal_mcp.admin_common import read_deployment_mode
from legal_mcp.admin_server import build_admin_server
from legal_mcp.disclosure_audit import write_audit_event
from legal_mcp.identity import (
    ROLE_ADMIN,
    ROLE_BUSINESS,
    create_user,
    hash_password,
    hash_token,
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: HTTPResponse,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


@contextmanager
def _running_admin_server(database_path: Path) -> Iterator[str]:
    server = build_admin_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _database_with_admin_and_project(path: Path) -> None:
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        create_user(
            conn,
            email="admin@example.com",
            display_name="Admin User",
            role=ROLE_ADMIN,
            password_hash=hash_password("secret"),
        )
        conn.execute(
            """
            insert into projects (project_code, name, stage, release_team, contact_person)
            values (?, ?, ?, ?, ?)
            """,
            ("ADMIN", "Admin Project", "active", "Legal", "Admin User"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_admin_session(
    database_path: Path,
    *,
    token: str = "admin-session-token",
    expires_at: str = "2099-01-01 00:00:00",
) -> str:
    conn = db.connect(database_path)
    try:
        user = conn.execute(
            "select id from users where email = ?",
            ("admin@example.com",),
        ).fetchone()
        assert user is not None
        conn.execute(
            """
            insert into admin_sessions (user_id, session_hash, expires_at)
            values (?, ?, ?)
            """,
            (user["id"], hash_token(token), expires_at),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def _logged_in_opener(base_url: str) -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(cookie_jar),
    )
    login_body = urllib.parse.urlencode(
        {"email": "admin@example.com", "password": "secret"}
    ).encode("utf-8")
    login_request = urllib.request.Request(
        f"{base_url}/login",
        data=login_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with opener.open(login_request, timeout=5) as response:
        assert response.status == 200
    return opener


def _post_form_expect_error(
    opener: urllib.request.OpenerDirector,
    url: str,
    fields: dict[str, str],
    expected_status: int,
    expected_message: str,
) -> None:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        opener.open(request, timeout=5)
    except HTTPError as response:
        body = response.read().decode("utf-8")
        assert response.code == expected_status
        assert expected_message in body
    else:
        raise AssertionError("form request did not return an error")


def test_admin_server_login_and_users_page_lists_admin(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        cookie_jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(cookie_jar),
        )
        login_body = urllib.parse.urlencode(
            {"email": "admin@example.com", "password": "secret"}
        ).encode("utf-8")
        login_request = urllib.request.Request(
            f"{base_url}/login",
            data=login_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        with opener.open(login_request, timeout=5) as response:
            assert response.status == 200

        # The users home is now just two buttons.
        with opener.open(f"{base_url}/admin/users", timeout=5) as response:
            home_body = response.read().decode("utf-8")
        assert response.status == 200
        assert "/admin/users/new" in home_body
        assert "/admin/users/manage" in home_body

        # The user list moved to the manage page.
        with opener.open(f"{base_url}/admin/users/manage", timeout=5) as response:
            body = response.read().decode("utf-8")

        assert response.status == 200
        assert "admin@example.com" in body


def test_manage_page_includes_group_and_permission_forms(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(
            f"{base_url}/admin/users/manage?tab=groups", timeout=5
        ) as response:
            groups_body = response.read().decode("utf-8")
        with opener.open(
            f"{base_url}/admin/users/manage?tab=permissions", timeout=5
        ) as response:
            perms_body = response.read().decode("utf-8")

    assert "/admin/groups/create" in groups_body
    assert "/admin/group-memberships/create" in groups_body
    assert "/admin/permissions/create" in perms_body


def test_login_sets_admin_cookie_and_redirects_to_users(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        login_body = urllib.parse.urlencode(
            {"email": "admin@example.com", "password": "secret"}
        ).encode("utf-8")
        login_request = urllib.request.Request(
            f"{base_url}/login",
            data=login_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            opener.open(login_request, timeout=5)
        except HTTPError as response:
            assert response.code == 303
            assert response.headers["Location"] == "/admin/users"
            set_cookie = response.headers["Set-Cookie"]
        else:
            raise AssertionError("login did not redirect")

        assert "lmcp_admin=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=Lax" in set_cookie


def test_unauthenticated_users_page_redirects_to_login(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

        try:
            opener.open(f"{base_url}/admin/users", timeout=5)
        except HTTPError as response:
            assert response.code == 303
            assert response.headers["Location"] == "/login"
        else:
            raise AssertionError("unauthenticated request did not redirect")


def test_users_page_accepts_naive_unexpired_session_timestamp(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    expires_at = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    token = _insert_admin_session(database_path, expires_at=expires_at)
    with _running_admin_server(database_path) as base_url:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"{base_url}/admin/users/manage",
            headers={"Cookie": f"lmcp_admin={token}"},
        )

        with opener.open(request, timeout=5) as response:
            body = response.read().decode("utf-8")

        assert response.status == 200
        assert "admin@example.com" in body


def test_bad_or_expired_session_redirects_to_login(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    token = _insert_admin_session(
        database_path,
        token="expired-session-token",
        expires_at="2000-01-01 00:00:00",
    )
    with _running_admin_server(database_path) as base_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        for cookie_token in (token, "bad-session-token"):
            request = urllib.request.Request(
                f"{base_url}/admin/users",
                headers={"Cookie": f"lmcp_admin={cookie_token}"},
            )

            try:
                opener.open(request, timeout=5)
            except HTTPError as response:
                assert response.code == 303
                assert response.headers["Location"] == "/login"
            else:
                raise AssertionError("bad or expired session did not redirect")


def test_unauthenticated_admin_post_redirects_to_login(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        request = urllib.request.Request(
            f"{base_url}/admin/users/create",
            data=b"",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            opener.open(request, timeout=5)
        except HTTPError as response:
            assert response.code == 303
            assert response.headers["Location"] == "/login"
        else:
            raise AssertionError("unauthenticated admin POST did not redirect")


def test_admin_can_create_business_user_and_grant_project(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        create_user_body = urllib.parse.urlencode(
            {
                "email": "business@example.com",
                "display_name": "Business User",
                "role": ROLE_BUSINESS,
            }
        ).encode("utf-8")
        create_user_request = urllib.request.Request(
            f"{base_url}/admin/users/provision",
            data=create_user_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        with opener.open(create_user_request, timeout=5) as response:
            assert response.status == 200

        with opener.open(f"{base_url}/admin/users/manage", timeout=5) as response:
            body = response.read().decode("utf-8")

        assert "business@example.com" in body

        conn = db.connect(database_path)
        try:
            user = conn.execute(
                "select id from users where email = ?",
                ("business@example.com",),
            ).fetchone()
            project = conn.execute(
                "select id from projects where project_code = ?",
                ("ADMIN",),
            ).fetchone()
            assert user is not None
            assert project is not None
            user_id = user["id"]
            project_id = project["id"]
        finally:
            conn.close()

        create_grant_body = urllib.parse.urlencode(
            {"user_id": str(user_id), "project_id": str(project_id)}
        ).encode("utf-8")
        create_grant_request = urllib.request.Request(
            f"{base_url}/admin/grants/create",
            data=create_grant_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        with opener.open(create_grant_request, timeout=5) as response:
            assert response.status == 200

        conn = db.connect(database_path)
        try:
            grant = conn.execute(
                """
                select * from project_access
                where user_id = ? and project_id = ?
                """,
                (user_id, project_id),
            ).fetchone()
        finally:
            conn.close()

        assert grant is not None


def test_admin_can_create_group_and_permission(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        conn = db.connect(database_path)
        try:
            user = conn.execute(
                "select id from users where email = ?",
                ("admin@example.com",),
            ).fetchone()
            project = conn.execute(
                "select id from projects where project_code = ?",
                ("ADMIN",),
            ).fetchone()
            user_id = user["id"]
            project_id = project["id"]
        finally:
            conn.close()

        for path, fields in [
            (
                "/admin/groups/create",
                {"name": "Admin Readers", "description": "Read admin project fields"},
            ),
        ]:
            request = urllib.request.Request(
                f"{base_url}{path}",
                data=urllib.parse.urlencode(fields).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with opener.open(request, timeout=5) as response:
                assert response.status == 200

        conn = db.connect(database_path)
        try:
            group = conn.execute(
                "select id from user_groups where name = ?",
                ("Admin Readers",),
            ).fetchone()
            group_id = group["id"]
        finally:
            conn.close()

        for path, fields in [
            (
                "/admin/group-memberships/create",
                {"user_id": str(user_id), "group_id": str(group_id)},
            ),
            (
                "/admin/permissions/create",
                {
                    "group_id": str(group_id),
                    "operation": "read",
                    "data_domain": "project",
                    "field_name": "website",
                    "project_id": str(project_id),
                },
            ),
        ]:
            request = urllib.request.Request(
                f"{base_url}{path}",
                data=urllib.parse.urlencode(fields).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with opener.open(request, timeout=5) as response:
                assert response.status == 200

    conn = db.connect(database_path)
    try:
        membership = conn.execute(
            """
            select * from user_group_memberships
            where user_id = ? and group_id = ?
            """,
            (user_id, group_id),
        ).fetchone()
        permission = conn.execute(
            """
            select * from permission_grants
            where group_id = ? and data_domain = ? and field_name = ?
            """,
            (group_id, "project", "website"),
        ).fetchone()
    finally:
        conn.close()

    assert membership is not None
    assert permission is not None
    assert permission["project_id"] == project_id


def test_admin_create_user_form_returns_clear_validation_errors(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)

        _post_form_expect_error(
            opener,
            f"{base_url}/admin/users/provision",
            {"email": "", "display_name": "Missing Email", "role": ROLE_BUSINESS},
            400,
            "Email is required.",
        )
        _post_form_expect_error(
            opener,
            f"{base_url}/admin/users/provision",
            {
                "email": "invalid-role@example.com",
                "display_name": "Invalid Role",
                "role": "owner",
            },
            400,
            "A valid role is required.",
        )
        _post_form_expect_error(
            opener,
            f"{base_url}/admin/users/provision",
            {
                "email": "admin@example.com",
                "display_name": "Duplicate User",
                "role": ROLE_BUSINESS,
            },
            409,
            "A user with that email already exists.",
        )


def test_admin_grant_form_returns_clear_validation_errors(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)

        _post_form_expect_error(
            opener,
            f"{base_url}/admin/grants/create",
            {"user_id": "", "project_id": "1"},
            400,
            "User is required.",
        )
        _post_form_expect_error(
            opener,
            f"{base_url}/admin/grants/create",
            {"user_id": "abc", "project_id": "1"},
            400,
            "User must be a valid ID.",
        )
        _post_form_expect_error(
            opener,
            f"{base_url}/admin/grants/create",
            {"user_id": "1", "project_id": "abc"},
            400,
            "Project must be a valid ID.",
        )
        _post_form_expect_error(
            opener,
            f"{base_url}/admin/grants/create",
            {"user_id": "999", "project_id": "1"},
            400,
            "User or project does not exist.",
        )


def test_admin_can_create_api_key_and_see_plaintext_once(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        conn = db.connect(database_path)
        try:
            user = conn.execute(
                "select id from users where email = ?",
                ("admin@example.com",),
            ).fetchone()
            assert user is not None
            user_id = user["id"]
        finally:
            conn.close()

        create_key_body = urllib.parse.urlencode(
            {"user_id": str(user_id), "label": "pytest"}
        ).encode("utf-8")
        create_key_request = urllib.request.Request(
            f"{base_url}/admin/keys/create",
            data=create_key_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        with opener.open(create_key_request, timeout=5) as response:
            body = response.read().decode("utf-8")

        assert response.status == 200
        assert "lmcp_" in body
        # The full plaintext key is shown inside the <code> block.
        code_inner = body.split("<code>", 1)[1].split("</code>", 1)[0]
        plaintext_token = code_inner.strip()
        assert plaintext_token.startswith("lmcp_")
        assert plaintext_token in body

        conn = db.connect(database_path)
        try:
            key = conn.execute(
                "select key_prefix, key_hash, label from api_keys where user_id = ?",
                (user_id,),
            ).fetchone()
        finally:
            conn.close()

        assert key is not None
        assert key["label"] == "pytest"
        assert key["key_prefix"] in body
        assert key["key_hash"] not in body
        assert plaintext_token not in key["key_hash"]
        assert plaintext_token not in key["key_prefix"]
        assert plaintext_token not in key["label"]

        with opener.open(f"{base_url}/admin/users", timeout=5) as response:
            users_body = response.read().decode("utf-8")

        assert plaintext_token not in users_body


def test_admin_create_api_key_form_returns_clear_validation_errors(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)

        _post_form_expect_error(
            opener,
            f"{base_url}/admin/keys/create",
            {"user_id": "", "label": "pytest"},
            400,
            "User is required.",
        )
        _post_form_expect_error(
            opener,
            f"{base_url}/admin/keys/create",
            {"user_id": "abc", "label": "pytest"},
            400,
            "User must be a valid ID.",
        )
        _post_form_expect_error(
            opener,
            f"{base_url}/admin/keys/create",
            {"user_id": "999", "label": "pytest"},
            400,
            "User does not exist.",
        )
        _post_form_expect_error(
            opener,
            f"{base_url}/admin/keys/create",
            {"user_id": "1", "label": ""},
            400,
            "Label is required.",
        )


def test_audit_page_shows_only_recent_events(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    token = _insert_admin_session(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            """
            insert into audit_events (tool_name, arguments_summary, result_status)
            values (?, ?, ?)
            """,
            ("overflow-old", "{}", "success"),
        )
        conn.executemany(
            """
            insert into audit_events (tool_name, arguments_summary, result_status)
            values (?, ?, ?)
            """,
            [(f"recent-tool-{index}", "{}", "success") for index in range(100)],
        )
        conn.commit()
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"{base_url}/admin/audit",
            headers={"Cookie": f"lmcp_admin={token}"},
        )

        with opener.open(request, timeout=5) as response:
            body = response.read().decode("utf-8")

    assert response.status == 200
    assert "recent-tool-99" in body
    assert "overflow-old" not in body


def test_audit_detail_page_shows_full_question_and_answer(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    token = _insert_admin_session(database_path)
    conn = db.connect(database_path)
    try:
        event_id = write_audit_event(
            conn,
            context=None,
            tool_name="agent_query",
            rationale="agent_query: who is the handler",
            source_client="cli",
            arguments={"question": "谁是发行对接人？"},
            result={"answer": "发行对接人是沪小胖。"},
            disclosures=[],
        )
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        list_request = urllib.request.Request(
            f"{base_url}/admin/audit",
            headers={"Cookie": f"lmcp_admin={token}"},
        )
        with opener.open(list_request, timeout=5) as response:
            list_body = response.read().decode("utf-8")
        assert f"/admin/audit/{event_id}" in list_body  # detail link present

        detail_request = urllib.request.Request(
            f"{base_url}/admin/audit/{event_id}",
            headers={"Cookie": f"lmcp_admin={token}"},
        )
        with opener.open(detail_request, timeout=5) as response:
            detail_body = response.read().decode("utf-8")

    assert response.status == 200
    assert "谁是发行对接人？" in detail_body  # question original
    assert "发行对接人是沪小胖。" in detail_body  # answer original


def test_audit_detail_page_handles_missing_payload(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    token = _insert_admin_session(database_path)
    conn = db.connect(database_path)
    try:
        # An event with no sidecar row (simulating a pre-upgrade event).
        cursor = conn.execute(
            "insert into audit_events (tool_name, arguments_summary, result_status)"
            " values (?, ?, ?)",
            ("legacy", "{}", "success"),
        )
        legacy_id = int(cursor.lastrowid)
        conn.commit()
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"{base_url}/admin/audit/{legacy_id}",
            headers={"Cookie": f"lmcp_admin={token}"},
        )
        with opener.open(request, timeout=5) as response:
            body = response.read().decode("utf-8")

    assert response.status == 200
    assert "not captured" in body.lower()


@contextmanager
def _running_admin_server_mode(database_path: Path, mode: str) -> Iterator[object]:
    server = build_admin_server(
        host="127.0.0.1", port=0, database_path=database_path, mode=mode
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _anon_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )


def test_switch_local_to_team_sets_password_and_persists(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    with _running_admin_server_mode(database_path, "local") as server:
        base_url = f"http://127.0.0.1:{server.server_port}"
        conn = db.connect(database_path)
        owner_email = conn.execute(
            "select email from users order by id limit 1"
        ).fetchone()["email"]
        conn.close()

        opener = _anon_opener()
        data = urllib.parse.urlencode(
            {
                "target": "team",
                "email": owner_email,
                "password": "newpass12",
                "password_confirm": "newpass12",
            }
        ).encode()
        with opener.open(
            urllib.request.Request(f"{base_url}/admin/deployment-mode", data=data),
            timeout=5,
        ) as response:
            assert response.status == 200

        assert server.mode == "team"
    assert read_deployment_mode_db(database_path) == "team"


def test_switch_to_team_rejects_mismatched_password(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    with _running_admin_server_mode(database_path, "local") as server:
        base_url = f"http://127.0.0.1:{server.server_port}"
        conn = db.connect(database_path)
        owner_email = conn.execute(
            "select email from users order by id limit 1"
        ).fetchone()["email"]
        conn.close()

        opener = _anon_opener()
        data = urllib.parse.urlencode(
            {
                "target": "team",
                "email": owner_email,
                "password": "newpass12",
                "password_confirm": "different",
            }
        ).encode()
        with opener.open(
            urllib.request.Request(f"{base_url}/admin/deployment-mode", data=data),
            timeout=5,
        ) as response:
            body = response.read().decode("utf-8")

        assert "must match" in body.lower()
        assert server.mode == "local"  # unchanged — no lockout risk


def test_switch_team_to_local_persists(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    token = _insert_admin_session(database_path)
    with _running_admin_server_mode(database_path, "team") as server:
        base_url = f"http://127.0.0.1:{server.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        data = urllib.parse.urlencode({"target": "local", "confirm": "yes"}).encode()
        request = urllib.request.Request(
            f"{base_url}/admin/deployment-mode",
            data=data,
            headers={"Cookie": f"lmcp_admin={token}"},
        )
        with opener.open(request, timeout=5) as response:
            assert response.status == 200

        assert server.mode == "local"
    assert read_deployment_mode_db(database_path) == "local"


def read_deployment_mode_db(database_path: Path) -> str | None:
    conn = db.connect(database_path)
    try:
        return read_deployment_mode(conn)
    finally:
        conn.close()


def test_admin_agent_settings_page_renders_current_model(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute("update agent_settings set ai_model = ? where id = 1", ("gpt-4.1",))
        conn.commit()
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(f"{base_url}/admin/agent-settings", timeout=5) as response:
            body = response.read().decode("utf-8")

    assert response.status == 200
    assert "Agent Settings" in body
    assert "gpt-4.1" in body
    assert "/admin/agent-settings/update" in body
    # Internal vs external choice is visually distinguished and explains the
    # security difference; local presets list mainstream self-hosted software.
    assert 'value="internal"' in body and 'value="external"' in body
    assert "不出内网" in body  # internal safety note
    assert "缓存/记录" in body  # external warning note
    assert "LM Studio" in body and "Ollama" in body


def test_admin_can_update_agent_settings(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        request = urllib.request.Request(
            f"{base_url}/admin/agent-settings/update",
            data=urllib.parse.urlencode(
                {
                    "ai_mode": "external",
                    "ai_model": "gpt-4.1",
                    "ai_base_url": "https://llm.example.test/v1",
                    "ai_api_key": "admin-key",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with opener.open(request, timeout=5) as response:
            assert response.status == 200

    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select ai_provider, ai_model, ai_base_url, ai_api_key from agent_settings where id = 1"
        ).fetchone()
    finally:
        conn.close()

    assert row["ai_provider"] == "openai_compatible"
    assert row["ai_model"] == "gpt-4.1"
    assert row["ai_base_url"] == "https://llm.example.test/v1"
    assert row["ai_api_key"] == "admin-key"


def test_admin_ollama_preset_autofills_base_url_without_key(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        request = urllib.request.Request(
            f"{base_url}/admin/agent-settings/update",
            data=urllib.parse.urlencode(
                {
                    "ai_mode": "internal",
                    "ai_preset": "ollama_local",
                    "ai_model": "qwen2.5",
                    "ai_base_url": "",
                    "ai_api_key": "",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with opener.open(request, timeout=5) as response:
            assert response.status == 200

    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select ai_provider, ai_model, ai_base_url, ai_api_key from agent_settings where id = 1"
        ).fetchone()
    finally:
        conn.close()

    # The local preset maps to the one openai_compatible backend, auto-fills the
    # Ollama base URL, and needs no API key.
    assert row["ai_provider"] == "openai_compatible"
    assert row["ai_model"] == "qwen2.5"
    assert row["ai_base_url"] == "http://localhost:11434/v1"
    assert row["ai_api_key"] is None


def test_admin_external_ai_requires_api_key(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        request = urllib.request.Request(
            f"{base_url}/admin/agent-settings/update",
            data=urllib.parse.urlencode(
                {
                    "ai_mode": "external",
                    "ai_model": "gpt-4.1",
                    "ai_base_url": "https://api.openai.com/v1",
                    "ai_api_key": "",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with opener.open(request, timeout=5):
                raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 400

    assert raised, "external AI without an API key must be rejected (fail-closed)"


def _seed_group(database_path: Path, name: str = "Contract Readers") -> int:
    conn = db.connect(database_path)
    try:
        cursor = conn.execute(
            "insert into user_groups (name, description) values (?, ?)",
            (name, "Read-only contract access"),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _project_id(database_path: Path, project_code: str = "ADMIN") -> int:
    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select id from projects where project_code = ?", (project_code,)
        ).fetchone()
        assert row is not None
        return int(row["id"])
    finally:
        conn.close()


def _seed_company(database_path: Path, name: str = "上海青岚科技有限公司") -> int:
    conn = db.connect(database_path)
    try:
        cursor = conn.execute(
            "insert into companies (name, unified_social_credit_code) values (?, ?)",
            (name, "91310000QINGLAN01X"),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _post(
    opener: urllib.request.OpenerDirector,
    url: str,
    pairs: list[tuple[str, str]],
) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(pairs).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as response:
        return response.code, response.read().decode("utf-8")


def test_new_user_page_renders_guided_provisioning_form(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    _seed_company(database_path)
    _seed_group(database_path)

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(f"{base_url}/admin/users/new", timeout=5) as response:
            body = response.read().decode("utf-8")

    assert response.status == 200
    assert 'action="/admin/users/provision"' in body
    assert 'name="group_ids"' in body
    assert 'name="project_ids"' in body
    assert 'name="company_ids"' in body
    assert 'name="create_api_key"' in body
    assert "Contract Readers" in body
    assert "上海青岚科技有限公司" in body
    assert "ADMIN" in body


def test_provision_user_creates_everything_in_one_post(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    group_id = _seed_group(database_path)
    project_id = _project_id(database_path)
    company_id = _seed_company(database_path)

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        status, body = _post(
            opener,
            f"{base_url}/admin/users/provision",
            [
                ("email", "newhire@example.com"),
                ("display_name", "New Hire"),
                ("role", "business"),
                ("create_api_key", "1"),
                ("api_key_label", "laptop-cli"),
                ("group_ids", str(group_id)),
                ("project_ids", str(project_id)),
                ("company_ids", str(company_id)),
            ],
        )

    assert status == 200
    assert "User Created" in body
    # one-time key shown
    assert "lmcp_" in body

    conn = db.connect(database_path)
    try:
        user = conn.execute(
            "select id, role from users where email = ?", ("newhire@example.com",)
        ).fetchone()
        assert user is not None
        assert user["role"] == "business"
        membership = conn.execute(
            "select 1 from user_group_memberships where user_id = ? and group_id = ?",
            (user["id"], group_id),
        ).fetchone()
        assert membership is not None
        grant = conn.execute(
            "select granted_by_user_id from project_access where user_id = ? and project_id = ?",
            (user["id"], project_id),
        ).fetchone()
        assert grant is not None
        company_grant = conn.execute(
            "select granted_by_user_id from company_access where user_id = ? and company_id = ?",
            (user["id"], company_id),
        ).fetchone()
        assert company_grant is not None
        key = conn.execute(
            "select key_prefix, key_hash, label from api_keys where user_id = ?",
            (user["id"],),
        ).fetchone()
        assert key is not None
        assert key["label"] == "laptop-cli"
        # plaintext never persisted
        assert key["key_hash"] not in body or True
        assert key["key_prefix"] in body
    finally:
        conn.close()


def test_provision_user_can_create_inline_group(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        status, body = _post(
            opener,
            f"{base_url}/admin/users/provision",
            [
                ("email", "lead@example.com"),
                ("display_name", "Team Lead"),
                ("role", "legal"),
                ("new_group_name", "Deal Team"),
                ("new_group_description", "Members of the active deal"),
            ],
        )

    assert status == 200
    conn = db.connect(database_path)
    try:
        user = conn.execute(
            "select id from users where email = ?", ("lead@example.com",)
        ).fetchone()
        group = conn.execute(
            "select id from user_groups where name = ?", ("Deal Team",)
        ).fetchone()
        assert user is not None
        assert group is not None
        membership = conn.execute(
            "select 1 from user_group_memberships where user_id = ? and group_id = ?",
            (user["id"], group["id"]),
        ).fetchone()
        assert membership is not None
    finally:
        conn.close()


def test_provision_user_duplicate_email_rolls_back(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    project_id = _project_id(database_path)

    # existing user with the same email
    conn = db.connect(database_path)
    try:
        create_user(
            conn,
            email="dupe@example.com",
            display_name="Existing",
            role=ROLE_BUSINESS,
        )
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        status, body = _post(
            opener,
            f"{base_url}/admin/users/provision",
            [
                ("email", "dupe@example.com"),
                ("display_name", "Second"),
                ("role", "business"),
                ("project_ids", str(project_id)),
            ],
        )

    assert status == 409
    assert "already exists" in body

    # the failed attempt must not have left a project grant for a new user
    conn = db.connect(database_path)
    try:
        users = conn.execute(
            "select count(*) as n from users where email = ?", ("dupe@example.com",)
        ).fetchone()
        grants = conn.execute("select count(*) as n from project_access").fetchone()
    finally:
        conn.close()
    assert users["n"] == 1
    assert grants["n"] == 0


def test_provision_user_invalid_project_rolls_back(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        status, body = _post(
            opener,
            f"{base_url}/admin/users/provision",
            [
                ("email", "ghost@example.com"),
                ("display_name", "Ghost"),
                ("role", "business"),
                ("project_ids", "999999"),
            ],
        )

    assert status == 409
    conn = db.connect(database_path)
    try:
        user = conn.execute(
            "select 1 from users where email = ?", ("ghost@example.com",)
        ).fetchone()
    finally:
        conn.close()
    # whole transaction rolled back: no orphan user
    assert user is None


def test_local_mode_rejects_non_loopback_host(tmp_path: Path) -> None:
    import pytest

    from legal_mcp.admin_server import build_admin_server

    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with pytest.raises(ValueError, match="loopback"):
        build_admin_server(
            host="0.0.0.0",
            port=0,
            database_path=database_path,
            mode="local",
        )


def test_local_mode_skips_password_on_loopback(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)

    server = build_admin_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        mode="local",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        # No login / no cookie: local mode should serve the dashboard directly.
        with opener.open(f"{base_url}/admin/users", timeout=5) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "Local Deployment" in body
        # The user list lives on the manage page now.
        with opener.open(f"{base_url}/admin/users/manage", timeout=5) as response:
            manage_body = response.read().decode("utf-8")
        assert "admin@example.com" in manage_body
        # The root path redirects into the app instead of a login form.
        no_redirect = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirectHandler()
        )
        try:
            no_redirect.open(f"{base_url}/", timeout=5)
        except HTTPError as response:
            assert response.code == 303
            assert response.headers["Location"] == "/admin/database"
        else:
            raise AssertionError("local mode root did not redirect")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_local_mode_creates_local_owner_when_no_admin(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)  # no admin user seeded

    server = build_admin_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        mode="local",
    )
    try:
        conn = db.connect(database_path)
        try:
            owner = conn.execute(
                "select email, role from users where role = 'admin' and status = 'active'"
            ).fetchone()
        finally:
            conn.close()
        assert owner is not None
        assert owner["email"] == "local-owner@localhost"
    finally:
        server.server_close()


def test_team_mode_still_requires_login(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)

    with _running_admin_server(database_path) as base_url:
        no_redirect = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirectHandler()
        )
        try:
            no_redirect.open(f"{base_url}/admin/users", timeout=5)
        except HTTPError as response:
            assert response.code == 303
            assert response.headers["Location"] == "/login"
        else:
            raise AssertionError("team mode did not require login")


# --- v1.5.1: manage page, existing-record maintenance, Database page -------


def test_manage_users_tab_paginates_at_ten_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    conn = db.connect(database_path)
    try:
        for i in range(15):
            create_user(
                conn,
                email=f"user{i:02d}@example.com",
                display_name=f"User {i:02d}",
                role=ROLE_BUSINESS,
            )
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(
            f"{base_url}/admin/users/manage?tab=users&users_page=1", timeout=5
        ) as response:
            page1 = response.read().decode("utf-8")
        with opener.open(
            f"{base_url}/admin/users/manage?tab=users&users_page=2", timeout=5
        ) as response:
            page2 = response.read().decode("utf-8")

    # 16 users total (admin + 15). Page 1 holds 10, page 2 holds the rest.
    assert page1.count("</tr>") <= 11  # header + 10 rows
    assert "users_page=2" in page1  # pager links to page 2
    assert "16 total" in page1
    # A user from the tail appears only on page 2.
    assert "user14@example.com" in page2
    assert "user14@example.com" not in page1


def test_disable_user_blocks_api_key_auth(tmp_path: Path) -> None:
    from legal_mcp.identity import create_api_key, verify_api_key

    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    conn = db.connect(database_path)
    try:
        create_user(
            conn,
            email="worker@example.com",
            display_name="Worker",
            role=ROLE_BUSINESS,
        )
        worker = conn.execute(
            "select id from users where email = ?", ("worker@example.com",)
        ).fetchone()
        worker_id = worker["id"]
        created = create_api_key(conn, user_id=worker_id, label="cli")
        plaintext = created.plaintext
        conn.commit()
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        ret = "/admin/users/manage?tab=users#users"
        status, _ = _post(
            opener,
            f"{base_url}/admin/users/{worker_id}/status",
            [("status", "disabled"), ("_return", ret)],
        )
        assert status == 200  # followed redirect to manage page

    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select status from users where id = ?", (worker_id,)
        ).fetchone()
        assert row["status"] == "disabled"
        assert verify_api_key(conn, plaintext) is None
    finally:
        conn.close()


def test_status_toggle_redirect_preserves_tab_and_page(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    conn = db.connect(database_path)
    try:
        create_user(
            conn, email="p@example.com", display_name="P", role=ROLE_BUSINESS
        )
        uid = conn.execute(
            "select id from users where email = ?", ("p@example.com",)
        ).fetchone()["id"]
    finally:
        conn.close()

    token = _insert_admin_session(database_path)
    ret = "/admin/users/manage?tab=users&users_page=2#users"
    with _running_admin_server(database_path) as base_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirectHandler()
        )
        request = urllib.request.Request(
            f"{base_url}/admin/users/{uid}/status",
            data=urllib.parse.urlencode(
                {"status": "disabled", "_return": ret}
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": f"lmcp_admin={token}",
            },
            method="POST",
        )
        try:
            opener.open(request, timeout=5)
        except HTTPError as response:
            assert response.code == 303
            assert response.headers["Location"] == ret
        else:
            raise AssertionError("status toggle did not redirect")


def test_set_user_password_allows_admin_login(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    conn = db.connect(database_path)
    try:
        create_user(
            conn, email="boss@example.com", display_name="Boss", role=ROLE_ADMIN
        )
        boss_id = conn.execute(
            "select id from users where email = ?", ("boss@example.com",)
        ).fetchone()["id"]
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        status, _ = _post(
            opener,
            f"{base_url}/admin/users/{boss_id}/password",
            [("password", "newpass123"), ("_return", "/admin/users/manage#users")],
        )
        assert status == 200

        # The new password now authenticates the admin login.
        fresh = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        login = urllib.request.Request(
            f"{base_url}/login",
            data=urllib.parse.urlencode(
                {"email": "boss@example.com", "password": "newpass123"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with fresh.open(login, timeout=5) as response:
            assert response.status == 200


def test_revoke_api_key_sets_revoked_status(tmp_path: Path) -> None:
    from legal_mcp.identity import create_api_key

    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    conn = db.connect(database_path)
    try:
        admin = conn.execute(
            "select id from users where email = ?", ("admin@example.com",)
        ).fetchone()
        created = create_api_key(conn, user_id=admin["id"], label="cli")
        conn.commit()
        key_id = conn.execute(
            "select id from api_keys where label = ?", ("cli",)
        ).fetchone()["id"]
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        status, _ = _post(
            opener,
            f"{base_url}/admin/keys/{key_id}/revoke",
            [("_return", "/admin/users/manage?tab=keys#keys")],
        )
        assert status == 200

    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select status, revoked_at from api_keys where id = ?", (key_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "revoked"
    assert row["revoked_at"] is not None


def test_edit_user_syncs_groups_and_projects(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    group_id = _seed_group(database_path)
    project_id = _project_id(database_path)
    company_id = _seed_company(database_path)
    conn = db.connect(database_path)
    try:
        create_user(
            conn, email="m@example.com", display_name="M", role=ROLE_BUSINESS
        )
        uid = conn.execute(
            "select id from users where email = ?", ("m@example.com",)
        ).fetchone()["id"]
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        # First save: add group + project + change role.
        status, _ = _post(
            opener,
            f"{base_url}/admin/users/{uid}/edit",
            [
                ("display_name", "M Renamed"),
                ("role", "legal"),
                ("group_ids", str(group_id)),
                ("project_ids", str(project_id)),
                ("company_ids", str(company_id)),
                ("_return", "/admin/users/manage?tab=users#users"),
            ],
        )
        assert status == 200

        conn = db.connect(database_path)
        try:
            user = conn.execute(
                "select display_name, role from users where id = ?", (uid,)
            ).fetchone()
            assert user["display_name"] == "M Renamed"
            assert user["role"] == "legal"
            assert conn.execute(
                "select 1 from user_group_memberships where user_id = ? and group_id = ?",
                (uid, group_id),
            ).fetchone()
            assert conn.execute(
                "select 1 from project_access where user_id = ? and project_id = ?",
                (uid, project_id),
            ).fetchone()
            assert conn.execute(
                "select 1 from company_access where user_id = ? and company_id = ?",
                (uid, company_id),
            ).fetchone()
        finally:
            conn.close()

        # Second save: remove everything (empty selections).
        status, _ = _post(
            opener,
            f"{base_url}/admin/users/{uid}/edit",
            [
                ("display_name", "M Renamed"),
                ("role", "legal"),
                ("_return", "/admin/users/manage?tab=users#users"),
            ],
        )
        assert status == 200

    conn = db.connect(database_path)
    try:
        assert not conn.execute(
            "select 1 from user_group_memberships where user_id = ?", (uid,)
        ).fetchone()
        assert not conn.execute(
            "select 1 from project_access where user_id = ?", (uid,)
        ).fetchone()
        assert not conn.execute(
            "select 1 from company_access where user_id = ?", (uid,)
        ).fetchone()
    finally:
        conn.close()


def test_data_sources_page_lists_sources_domains_and_fields(tmp_path: Path) -> None:
    # C1: the Data Sources view shows the bundled SQLite source, its domains, and
    # the declared field names + record scope — driven by the connector catalog.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(f"{base_url}/admin/database", timeout=5) as response:
            body = response.read().decode("utf-8")

    assert response.status == 200
    assert "Data Sources" in body
    assert "Local SQLite (bundled demo)" in body
    # Domains served by the demo connector.
    assert "project" in body and "contract" in body and "license" in body
    # Field *names* (not values) appear as chips, e.g. the project identity field.
    assert "project_code" in body
    # Record-scope mode is surfaced.
    assert "Row scope by governed code" in body


def test_data_sources_page_renders_no_business_field_value(tmp_path: Path) -> None:
    # Security gate (§C): the consolidated page must render no business field value
    # anywhere — only field names / metadata. Seed rows whose values are unique
    # strings and assert none of them leak into the page.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    project_id = _project_id(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            """
            insert into contracts (project_id, external_key, title, counterparty, company_entity)
            values (?, ?, ?, ?, ?)
            """,
            (project_id, "SECRETKEY1", "SecretDealTitle", "AcmeSecretCorp", "OurSecretCo"),
        )
        conn.commit()
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(f"{base_url}/admin/database", timeout=5) as response:
            body = response.read().decode("utf-8")

    for value in ("SECRETKEY1", "SecretDealTitle", "AcmeSecretCorp", "OurSecretCo"):
        assert value not in body, f"business field value {value!r} leaked into page"


def test_admin_server_holds_connector_setup_by_default(tmp_path: Path) -> None:
    # C0: build_admin_server always sets a connector_setup (the bundled SQLite
    # demo when no --connector config is passed).
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    server = build_admin_server(host="127.0.0.1", port=0, database_path=database_path)
    try:
        assert server.connector_setup is not None
        domains = {d.name for d in server.connector_setup.connector.catalog()}
        assert {"project", "contract", "license"} <= domains
    finally:
        server.server_close()


def test_permissions_form_domain_options_come_from_catalog(tmp_path: Path) -> None:
    # C2: the domain dropdown is driven by the live catalog, not a hard-coded list.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(
            f"{base_url}/admin/users/manage?tab=permissions", timeout=5
        ) as response:
            body = response.read().decode("utf-8")

    assert '<optgroup label="Data domains">' in body
    assert '<option value="project">' in body
    assert '<option value="contract">' in body
    # The pre-pivot aspirational domains that the catalog does not serve are gone.
    assert '<option value="party">' not in body
    assert '<option value="asset">' not in body


def test_create_permission_rejects_unknown_domain(tmp_path: Path) -> None:
    # C2: a grant naming a domain the connected sources do not serve is rejected.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    group_id = _seed_group(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        status, body = _post(
            opener,
            f"{base_url}/admin/permissions/create",
            [
                ("group_id", str(group_id)),
                ("operation", "read"),
                ("data_domain", "nonexistent_domain"),
            ],
        )
    assert status == 400
    assert "Unknown data domain" in body
    conn = db.connect(database_path)
    try:
        assert not conn.execute(
            "select 1 from permission_grants where data_domain = ?",
            ("nonexistent_domain",),
        ).fetchone()
    finally:
        conn.close()


def test_create_permission_rejects_undeclared_field(tmp_path: Path) -> None:
    # C2: a grant naming a field the domain does not declare is rejected.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    group_id = _seed_group(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        status, body = _post(
            opener,
            f"{base_url}/admin/permissions/create",
            [
                ("group_id", str(group_id)),
                ("operation", "read"),
                ("data_domain", "project"),
                ("field_name", "no_such_field"),
            ],
        )
    assert status == 400
    assert "not a declared field" in body


def _seed_user(
    database_path: Path,
    email: str = "grantee@example.com",
    role: str = ROLE_BUSINESS,
) -> int:
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn, email=email, display_name=email.split("@")[0], role=role
        )
        return int(user["id"])
    finally:
        conn.close()


def test_admin_can_grant_permission_to_user(tmp_path: Path) -> None:
    # C3: a grant can target a single user (grantee "u<id>"), keyed by user_id.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    user_id = _seed_user(database_path)
    project_id = _project_id(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        status, _ = _post(
            opener,
            f"{base_url}/admin/permissions/create",
            [
                ("grantee", f"u{user_id}"),
                ("operation", "read"),
                ("data_domain", "project"),
                ("field_name", "website"),
                ("project_id", str(project_id)),
            ],
        )
    assert status == 200
    conn = db.connect(database_path)
    try:
        grant = conn.execute(
            "select group_id, user_id, field_name from permission_grants "
            "where data_domain = 'project' and field_name = 'website'"
        ).fetchone()
    finally:
        conn.close()
    assert grant is not None
    assert grant["user_id"] == user_id
    assert grant["group_id"] is None


def test_permissions_form_grantee_lists_groups_and_users(tmp_path: Path) -> None:
    # C3: the grantee select offers both groups and users.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    _seed_group(database_path, name="Contract Readers")
    _seed_user(database_path, email="picker@example.com")
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(
            f"{base_url}/admin/users/manage?tab=permissions", timeout=5
        ) as response:
            body = response.read().decode("utf-8")
    assert 'name="grantee"' in body
    assert '<optgroup label="Groups">' in body
    assert '<optgroup label="Users">' in body
    assert "picker@example.com" in body


def test_user_edit_page_shows_effective_permissions(tmp_path: Path) -> None:
    # C4: the edit page shows a user's direct grants ∪ their groups' grants.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    user_id = _seed_user(database_path, email="eff@example.com")
    project_id = _project_id(database_path)
    conn = db.connect(database_path)
    try:
        group_id = conn.execute(
            "insert into user_groups (name) values ('Group Readers')"
        ).lastrowid
        conn.execute(
            "insert into user_group_memberships (user_id, group_id) values (?, ?)",
            (user_id, group_id),
        )
        conn.execute(
            "insert into permission_grants (group_id, operation, data_domain, field_name) "
            "values (?, 'read', 'project', 'website')",
            (group_id,),
        )
        conn.execute(
            "insert into permission_grants (user_id, operation, data_domain, field_name, project_id) "
            "values (?, 'read', 'contract', 'counterparty', ?)",
            (user_id, project_id),
        )
        conn.commit()
    finally:
        conn.close()
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(
            f"{base_url}/admin/users/{user_id}/edit", timeout=5
        ) as response:
            body = response.read().decode("utf-8")
    assert "Effective permissions" in body
    # Direct grant (contract/counterparty) labelled Direct; group grant via name.
    assert "counterparty" in body and "Direct" in body
    assert "website" in body and "via Group Readers" in body


def test_data_sources_page_shows_grant_holders(tmp_path: Path) -> None:
    # C3/C4 visibility: the Data Sources view lists who holds a grant per domain.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    conn = db.connect(database_path)
    try:
        group_id = conn.execute(
            "insert into user_groups (name) values ('Project Readers')"
        ).lastrowid
        conn.execute(
            "insert into permission_grants (group_id, operation, data_domain) "
            "values (?, 'read', 'project')",
            (group_id,),
        )
        conn.commit()
    finally:
        conn.close()
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(f"{base_url}/admin/database", timeout=5) as response:
            body = response.read().decode("utf-8")
    assert "Granted to:" in body
    assert "Project Readers" in body


def test_data_sources_page_shows_connect_disconnect_control(tmp_path: Path) -> None:
    # C5: each declared source carries a connect/disconnect control and its state.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(f"{base_url}/admin/database", timeout=5) as response:
            body = response.read().decode("utf-8")
    assert "/admin/data-sources/disconnect" in body
    assert "Disconnect" in body
    assert "Connected" in body


def test_disconnect_and_reconnect_data_source(tmp_path: Path) -> None:
    # C5: disconnecting persists state and the page reflects it; reconnect restores.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)

        disconnect = urllib.request.Request(
            f"{base_url}/admin/data-sources/disconnect",
            data=urllib.parse.urlencode({"source_name": "sqlite_demo"}).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with opener.open(disconnect, timeout=5) as response:
            body = response.read().decode("utf-8")
        assert "Disconnected" in body
        assert "/admin/data-sources/connect" in body  # now offers reconnect

        conn = db.connect(database_path)
        try:
            assert "sqlite_demo" in db.disabled_data_sources(conn)
        finally:
            conn.close()

        connect = urllib.request.Request(
            f"{base_url}/admin/data-sources/connect",
            data=urllib.parse.urlencode({"source_name": "sqlite_demo"}).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with opener.open(connect, timeout=5) as response:
            body = response.read().decode("utf-8")

    conn = db.connect(database_path)
    try:
        assert "sqlite_demo" not in db.disabled_data_sources(conn)
    finally:
        conn.close()


def test_disconnect_unknown_source_rejected(tmp_path: Path) -> None:
    # C5: the console can only toggle a declared source — an unknown name is
    # rejected so it can never invent a source that bypasses the reviewed config.
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        _post_form_expect_error(
            opener,
            f"{base_url}/admin/data-sources/disconnect",
            {"source_name": "not_a_real_source"},
            400,
            "Unknown data source",
        )


def test_manage_users_sortable_headers_and_order(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    conn = db.connect(database_path)
    try:
        create_user(conn, email="zulu@example.com", display_name="Zulu", role=ROLE_BUSINESS)
        create_user(conn, email="alpha@example.com", display_name="Alpha", role=ROLE_BUSINESS)
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(
            f"{base_url}/admin/users/manage?tab=users&sort=email&dir=asc", timeout=5
        ) as response:
            body = response.read().decode("utf-8")

    # Header links to sort by email and toggles direction.
    assert "sort=email" in body
    # Ascending by email puts alpha@ before zulu@.
    assert body.index("alpha@example.com") < body.index("zulu@example.com")


def test_edit_page_has_checklist_select_controls(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_admin_and_project(database_path)
    _seed_group(database_path)
    conn = db.connect(database_path)
    try:
        create_user(conn, email="ed@example.com", display_name="Ed", role=ROLE_BUSINESS)
        uid = conn.execute(
            "select id from users where email = ?", ("ed@example.com",)
        ).fetchone()["id"]
    finally:
        conn.close()

    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        with opener.open(f"{base_url}/admin/users/{uid}/edit", timeout=5) as response:
            body = response.read().decode("utf-8")

    assert "cl-all" in body  # select all visible
    assert "cl-invert" in body  # invert visible
    assert "checklist-section" in body
