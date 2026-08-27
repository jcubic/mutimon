"""Tests for secrets, JSON format, HTTP features, and auth."""

import json
import os
from unittest import mock

import pytest
import requests

from mutimon import main


def _http_error(status):
    """Build a requests.HTTPError whose response reports the given status."""
    err = requests.HTTPError()
    err.response = mock.MagicMock()
    err.response.status_code = status
    return err


# ========================= Secrets =========================


class TestLoadSecrets:
    def test_loads_valid_secrets(self, tmp_mutimon):
        secrets_file = tmp_mutimon / "secrets.json"
        secrets_file.write_text(json.dumps({"email": {"password": "secret123"}}))
        # Point SECRETS_FILE to temp
        with mock.patch.object(main, "SECRETS_FILE", str(secrets_file)):
            result = main.load_secrets()
        assert result["email"]["password"] == "secret123"

    def test_missing_file_returns_empty(self, tmp_mutimon):
        with mock.patch.object(main, "SECRETS_FILE", str(tmp_mutimon / "nope.json")):
            result = main.load_secrets()
        assert result == {}

    def test_invalid_json_returns_empty(self, tmp_mutimon):
        secrets_file = tmp_mutimon / "secrets.json"
        secrets_file.write_text("not json")
        with mock.patch.object(main, "SECRETS_FILE", str(secrets_file)):
            result = main.load_secrets()
        assert result == {}


class TestLiquidContext:
    def test_includes_secrets(self):
        main._secrets = {"api": {"key": "abc123"}}
        ctx = main.liquid_context({"q": "test"})
        assert ctx["q"] == "test"
        assert ctx["secret"]["api"]["key"] == "abc123"
        main._secrets = {}

    def test_includes_auth(self):
        ctx = main.liquid_context({"q": "test"}, auth_data={"token": "xyz"})
        assert ctx["auth"]["token"] == "xyz"

    def test_no_auth(self):
        ctx = main.liquid_context({"q": "test"})
        assert "auth" not in ctx


class TestSecretsMergeInRun:
    def test_email_password_from_secrets(self, tmp_mutimon, write_config, sample_config):
        write_config()
        secrets_file = tmp_mutimon / "secrets.json"
        secrets_file.write_text(json.dumps({"email": {"password": "from-secrets"}}))
        with mock.patch.object(main, "SECRETS_FILE", str(secrets_file)):
            with mock.patch("sys.argv", ["mon", "--validate"]):
                main.run()
        # After run, the config email password should be merged from secrets
        # (we can't easily check this, but it shouldn't crash)


class TestInitConfigCreatesSecrets:
    def test_creates_secrets_file(self, tmp_mutimon):
        secrets_file = tmp_mutimon / "secrets.json"
        if secrets_file.exists():
            secrets_file.unlink()
        with mock.patch.object(main, "SECRETS_FILE", str(secrets_file)):
            with pytest.raises(SystemExit):
                main.init_config()
        assert secrets_file.exists()
        data = json.loads(secrets_file.read_text())
        assert "email" in data


# ========================= JSON Format =========================


class TestFetchJson:
    def test_returns_parsed_json(self):
        fake_resp = mock.MagicMock()
        fake_resp.json.return_value = {"key": "value"}
        with mock.patch("mutimon.main.fetch_url", return_value=fake_resp):
            result = main.fetch_json("https://api.example.com/data")
        assert result == {"key": "value"}

    def test_passes_method_and_headers(self):
        fake_resp = mock.MagicMock()
        fake_resp.json.return_value = {}
        with mock.patch("mutimon.main.fetch_url", return_value=fake_resp) as mock_fetch:
            main.fetch_json(
                "https://api.example.com",
                method="POST",
                headers={"Auth": "Bearer x"},
                body={"q": "test"},
            )
        mock_fetch.assert_called_once_with(
            "https://api.example.com",
            method="POST",
            headers={"Auth": "Bearer x"},
            body={"q": "test"},
            user_agent=None,
        )


class TestParseJsonItems:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def test_list_type(self):
        data = {"items": [{"name": "a"}, {"name": "b"}]}
        spec = {
            "type": "list",
            "path": "items",
            "variables": {"name": {"path": "name"}},
        }
        items = main.parse_json_items(data, spec)
        assert len(items) == 2
        assert items[0]["name"] == "a"

    def test_single_type(self):
        data = {"info": {"title": "Test", "count": 42}}
        spec = {
            "type": "single",
            "path": "info",
            "variables": {"title": {"path": "title"}, "count": {"path": "count"}},
        }
        items = main.parse_json_items(data, spec)
        assert len(items) == 1
        assert items[0]["title"] == "Test"
        assert items[0]["count"] == 42

    def test_with_id(self):
        data = [{"slug": "abc", "val": 1}]
        spec = {
            "type": "list",
            "path": "",
            "id": {"source": "slug"},
            "variables": {"slug": {"path": "slug"}, "val": {"path": "val"}},
        }
        items = main.parse_json_items(data, spec)
        assert items[0]["id"] == "abc"

    def test_empty_result(self):
        data = {"items": []}
        spec = {
            "type": "list",
            "path": "items",
            "variables": {"name": {"path": "name"}},
        }
        items = main.parse_json_items(data, spec)
        assert items == []


class TestFetchAllItemsJson:
    def test_json_format(self, tmp_mutimon):
        main.setup_liquid({"defs": {}})
        definition = {
            "format": "json",
            "url": "https://api.example.com/stats",
            "query": {
                "type": "single",
                "path": "",
                "variables": {
                    "visitors": {"path": "visitors"},
                    "pageviews": {"path": "pageviews"},
                },
            },
        }
        fake_resp = mock.MagicMock()
        fake_resp.json.return_value = {"visitors": 100, "pageviews": 500}
        with mock.patch("mutimon.main.fetch_url", return_value=fake_resp):
            items = main.fetch_all_items(definition, {})
        assert len(items) == 1
        assert items[0]["visitors"] == 100
        assert items[0]["pageviews"] == 500

    def test_json_list(self, tmp_mutimon):
        main.setup_liquid({"defs": {}})
        definition = {
            "format": "json",
            "url": "https://api.example.com/metrics",
            "query": {
                "type": "list",
                "path": "data",
                "id": {"source": "page"},
                "variables": {
                    "page": {"path": "x"},
                    "views": {"path": "y"},
                },
            },
        }
        fake_resp = mock.MagicMock()
        fake_resp.json.return_value = {
            "data": [
                {"x": "/home", "y": 100},
                {"x": "/about", "y": 50},
            ]
        }
        with mock.patch("mutimon.main.fetch_url", return_value=fake_resp):
            items = main.fetch_all_items(definition, {})
        assert len(items) == 2
        assert items[0]["page"] == "/home"
        assert items[0]["id"] == "/home"


class TestFetchAllItemsJsonSources:
    """Multi-source definitions: several URLs merged into one item."""

    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def test_single_list_and_noquery_sources(self):
        definition = {
            "format": "json",
            "url": "https://api.example.com/x",
            "sources": [
                {
                    "name": "stats",
                    "url": "https://api.example.com/stats",
                    "query": {"type": "single", "variables": {"views": {"path": "views"}}},
                },
                {
                    "name": "pages",
                    "url": "https://api.example.com/pages",
                    "query": {"type": "list", "variables": {"p": {"path": "p"}}},
                },
                {"name": "raw", "url": "https://api.example.com/raw"},
            ],
            "query": {"type": "single", "id": {"source": "stats_views"}},
        }
        payloads = [{"views": 10}, [{"p": "a"}, {"p": "b"}], {"anything": 1}]
        with mock.patch("mutimon.main.fetch_json", side_effect=payloads):
            items = main.fetch_all_items(definition, {}, def_name="s")

        assert len(items) == 1
        item = items[0]
        assert item["stats_views"] == 10
        assert item["pages"] == [{"p": "a"}, {"p": "b"}]
        assert item["raw"] == {"anything": 1}
        assert item["id"] == 10

    def test_source_401_triggers_retry_auth(self):
        definition = {
            "format": "json",
            "url": "https://api.example.com/x",
            "auth": {
                "login": {"url": "https://auth", "extract": {}},
                "apply": {"headers": {"Authorization": "Bearer x"}},
            },
            "sources": [
                {
                    "name": "stats",
                    "url": "https://api.example.com/stats",
                    "query": {"type": "single", "variables": {"v": {"path": "v"}}},
                }
            ],
            "query": {"type": "single"},
        }
        with mock.patch("mutimon.main.resolve_auth", return_value=({}, {}, {"token": "t"})):
            with mock.patch(
                "mutimon.main.retry_auth",
                return_value=({"Authorization": "Bearer new"}, {}, {"token": "t2"}),
            ):
                with mock.patch(
                    "mutimon.main.fetch_json", side_effect=[_http_error(401), {"v": 5}]
                ):
                    items = main.fetch_all_items(definition, {}, def_name="s")
        assert items[0]["stats_v"] == 5

    def test_source_401_without_recovery_raises(self):
        definition = {
            "format": "json",
            "url": "https://api.example.com/x",
            "auth": {"login": {"url": "https://auth", "extract": {}}, "apply": {}},
            "sources": [
                {
                    "name": "stats",
                    "url": "https://api.example.com/stats",
                    "query": {"type": "single", "variables": {"v": {"path": "v"}}},
                }
            ],
            "query": {"type": "single"},
        }
        with mock.patch("mutimon.main.resolve_auth", return_value=({}, {}, {"token": "t"})):
            with mock.patch("mutimon.main.retry_auth", return_value=None):
                with mock.patch("mutimon.main.fetch_json", side_effect=_http_error(401)):
                    with pytest.raises(requests.HTTPError):
                        main.fetch_all_items(definition, {}, def_name="s")


class TestJsonValueQuery:
    """value.parse: json + value.query on an HTML page."""

    def test_scalar_json_yields_empty_list(self):
        html = '<div class="item"><script id="d">123</script></div>'
        query = {
            "type": "list",
            "selector": ".item",
            "variables": {
                "data": {
                    "selector": "#d",
                    "value": {
                        "type": "text",
                        "parse": "json",
                        "query": {"type": "list", "path": "x"},
                    },
                }
            },
        }
        items = main.parse_items(html, query)
        assert items[0]["data"] == []


# ========================= HTTP Features =========================


class TestFetchUrl:
    def test_get_request(self):
        fake_resp = mock.MagicMock()
        with mock.patch("mutimon.main.requests.request", return_value=fake_resp) as m:
            main.fetch_url("https://example.com")
        m.assert_called_once()
        assert m.call_args[0][0] == "GET"

    def test_post_with_body(self):
        fake_resp = mock.MagicMock()
        with mock.patch("mutimon.main.requests.request", return_value=fake_resp) as m:
            main.fetch_url("https://example.com", method="POST", body={"key": "val"})
        assert m.call_args[1]["json"] == {"key": "val"}

    def test_custom_headers(self):
        fake_resp = mock.MagicMock()
        with mock.patch("mutimon.main.requests.request", return_value=fake_resp) as m:
            main.fetch_url("https://example.com", headers={"X-Custom": "yes"})
        assert m.call_args[1]["headers"]["X-Custom"] == "yes"


class TestRenderHttpOptions:
    def setup_method(self):
        main.setup_liquid({"defs": {}})
        main._secrets = {}

    def test_renders_headers(self):
        definition = {"headers": {"Auth": "Bearer {{token}}"}}
        headers, body = main._render_http_options(definition, {"token": "abc"})
        assert headers["Auth"] == "Bearer abc"
        assert body is None

    def test_renders_body(self):
        definition = {"body": {"user": "{{name}}", "count": 5}}
        headers, body = main._render_http_options(definition, {"name": "test"})
        assert body["user"] == "test"
        assert body["count"] == 5

    def test_no_headers_or_body(self):
        headers, body = main._render_http_options({}, {})
        assert headers == {}
        assert body is None


# ========================= Auth =========================


class TestAuthCache:
    def test_save_and_load(self, tmp_mutimon):
        main._save_cached_auth("test-def", {"token": "abc123"})
        loaded = main._load_cached_auth("test-def")
        assert loaded["token"] == "abc123"

    def test_load_missing(self, tmp_mutimon):
        assert main._load_cached_auth("nonexistent") is None

    def test_load_corrupt(self, tmp_mutimon):
        path = main._auth_cache_path("bad")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("not json")
        assert main._load_cached_auth("bad") is None


class TestExtractAuthValues:
    def test_extract_from_json_body(self):
        resp = mock.MagicMock()
        resp.json.return_value = {"token": "jwt123", "user": {"id": 1}}
        extract = {
            "token": {"source": "body", "path": "token"},
            "uid": {"source": "body", "path": "user.id"},
        }
        result = main._extract_auth_values(resp, extract, "json")
        assert result["token"] == "jwt123"
        assert result["uid"] == 1

    def test_extract_from_cookie(self):
        resp = mock.MagicMock()
        resp.json.return_value = {}
        resp.cookies.get.return_value = "session123"
        extract = {"sid": {"source": "cookie", "name": "SESSIONID"}}
        result = main._extract_auth_values(resp, extract, "json")
        assert result["sid"] == "session123"

    def test_extract_from_header(self):
        resp = mock.MagicMock()
        resp.json.return_value = {}
        resp.headers.get.return_value = "csrf-token-value"
        extract = {"csrf": {"source": "header", "name": "X-CSRF-Token"}}
        result = main._extract_auth_values(resp, extract, "json")
        assert result["csrf"] == "csrf-token-value"

    def test_extract_from_html_body(self):
        resp = mock.MagicMock()
        resp.text = '<html><span id="token">abc123</span></html>'
        extract = {"token": {"source": "body", "selector": "#token"}}
        result = main._extract_auth_values(resp, extract, "html")
        assert result["token"] == "abc123"


class TestPerformAuthRequest:
    def setup_method(self):
        main.setup_liquid({"defs": {}})
        main._secrets = {"api": {"user": "admin", "pass": "secret"}}

    def teardown_method(self):
        main._secrets = {}

    def test_login_flow(self):
        fake_resp = mock.MagicMock()
        fake_resp.json.return_value = {"token": "jwt-token-123"}
        fake_resp.cookies.get.return_value = None
        fake_resp.headers.get.return_value = None

        login_spec = {
            "url": "https://api.example.com/auth/login",
            "method": "POST",
            "format": "json",
            "body": {
                "username": "{{secret.api.user}}",
                "password": "{{secret.api.pass}}",
            },
            "extract": {"token": {"source": "body", "path": "token"}},
        }
        with mock.patch("mutimon.main.fetch_url", return_value=fake_resp):
            result = main.perform_auth_request(login_spec, {})
        assert result["token"] == "jwt-token-123"

    def test_refresh_merges_existing(self):
        fake_resp = mock.MagicMock()
        fake_resp.json.return_value = {"access": "new-access"}
        fake_resp.cookies.get.return_value = None
        fake_resp.headers.get.return_value = None

        refresh_spec = {
            "url": "https://api.example.com/auth/refresh",
            "format": "json",
            "extract": {"access": {"source": "body", "path": "access"}},
        }
        existing = {"access": "old", "refresh": "keep-this"}
        with mock.patch("mutimon.main.fetch_url", return_value=fake_resp):
            result = main.perform_auth_request(refresh_spec, {}, existing)
        assert result["access"] == "new-access"
        assert result["refresh"] == "keep-this"


class TestResolveAuth:
    def setup_method(self):
        main.setup_liquid({"defs": {}})
        main._secrets = {}

    def test_no_auth(self):
        headers, cookies, data = main.resolve_auth({}, {})
        assert headers == {}
        assert cookies == {}
        assert data is None

    def test_cached_auth(self, tmp_mutimon):
        main._save_cached_auth("cached-def", {"token": "cached-jwt"})
        definition = {
            "auth": {
                "login": {"url": "https://example.com/login", "extract": {}},
                "apply": {"headers": {"Authorization": "Bearer {{auth.token}}"}},
            }
        }
        headers, cookies, data = main.resolve_auth(definition, {}, "cached-def")
        assert headers["Authorization"] == "Bearer cached-jwt"

    def test_login_when_no_cache(self, tmp_mutimon):
        fake_resp = mock.MagicMock()
        fake_resp.json.return_value = {"token": "fresh-jwt"}
        fake_resp.cookies.get.return_value = None
        fake_resp.headers.get.return_value = None

        definition = {
            "auth": {
                "login": {
                    "url": "https://example.com/login",
                    "format": "json",
                    "body": {"user": "x", "pass": "y"},
                    "extract": {"token": {"source": "body", "path": "token"}},
                },
                "apply": {"headers": {"Authorization": "Bearer {{auth.token}}"}},
            }
        }
        with mock.patch("mutimon.main.fetch_url", return_value=fake_resp):
            headers, cookies, data = main.resolve_auth(
                definition, {}, "login-def"
            )
        assert headers["Authorization"] == "Bearer fresh-jwt"
        # Should be cached
        cached = main._load_cached_auth("login-def")
        assert cached["token"] == "fresh-jwt"

    def test_apply_cookies(self, tmp_mutimon):
        main._save_cached_auth("cookie-def", {"sid": "sess123"})
        definition = {
            "auth": {
                "login": {"url": "https://example.com/login", "extract": {}},
                "apply": {"cookies": {"SESSIONID": "{{auth.sid}}"}},
            }
        }
        headers, cookies, data = main.resolve_auth(definition, {}, "cookie-def")
        assert cookies["SESSIONID"] == "sess123"


class TestRetryAuth:
    def setup_method(self):
        main.setup_liquid({"defs": {}})
        main._secrets = {}

    def test_refresh_succeeds(self, tmp_mutimon):
        fake_resp = mock.MagicMock()
        fake_resp.json.return_value = {"token": "refreshed"}
        fake_resp.cookies.get.return_value = None
        fake_resp.headers.get.return_value = None

        auth_spec = {
            "refresh": {
                "url": "https://example.com/refresh",
                "format": "json",
                "extract": {"token": {"source": "body", "path": "token"}},
            },
            "apply": {"headers": {"Authorization": "Bearer {{auth.token}}"}},
        }
        with mock.patch("mutimon.main.fetch_url", return_value=fake_resp):
            result = main.retry_auth(
                auth_spec, {}, "retry-def", {"token": "old", "refresh": "rt"}
            )
        assert result is not None
        assert result[0]["Authorization"] == "Bearer refreshed"

    def test_refresh_fails_falls_back_to_login(self, tmp_mutimon):
        login_resp = mock.MagicMock()
        login_resp.json.return_value = {"token": "new-login"}
        login_resp.cookies.get.return_value = None
        login_resp.headers.get.return_value = None

        auth_spec = {
            "login": {
                "url": "https://example.com/login",
                "format": "json",
                "body": {"user": "x"},
                "extract": {"token": {"source": "body", "path": "token"}},
            },
            "refresh": {
                "url": "https://example.com/refresh",
                "format": "json",
                "extract": {"token": {"source": "body", "path": "token"}},
            },
            "apply": {"headers": {"Authorization": "Bearer {{auth.token}}"}},
        }

        call_count = [0]

        def fake_fetch(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("refresh failed")
            return login_resp

        with mock.patch("mutimon.main.fetch_url", side_effect=fake_fetch):
            result = main.retry_auth(
                auth_spec, {}, "retry-login-def", {"token": "expired"}
            )
        assert result is not None
        assert result[0]["Authorization"] == "Bearer new-login"

    def test_no_login_no_refresh(self):
        result = main.retry_auth({}, {}, "x", None)
        assert result is None


class TestFetchAllItemsAuthRetry:
    """A 401 from a single-URL fetch re-authenticates and retries once."""

    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def test_single_json_401_retry(self):
        definition = {
            "format": "json",
            "url": "https://api.example.com/x",
            "auth": {"login": {"url": "https://auth", "extract": {}}, "apply": {}},
            "query": {"type": "single", "variables": {"v": {"path": "v"}}},
        }
        with mock.patch("mutimon.main.resolve_auth", return_value=({}, {}, {"token": "t"})):
            with mock.patch(
                "mutimon.main.retry_auth", return_value=({}, {}, {"token": "t2"})
            ):
                with mock.patch(
                    "mutimon.main.fetch_json", side_effect=[_http_error(401), {"v": 9}]
                ):
                    items = main.fetch_all_items(definition, {}, def_name="s")
        assert items[0]["v"] == 9

    def test_html_401_retry(self):
        definition = {
            "url": "https://example.com",
            "auth": {"login": {"url": "https://auth", "extract": {}}, "apply": {}},
            "query": {
                "type": "single",
                "selector": "div",
                "variables": {"t": {"selector": "span", "value": {"type": "text"}}},
            },
        }
        html = "<div><span>hi</span></div>"
        locale = main.Locale.parse("en")
        with mock.patch("mutimon.main.resolve_auth", return_value=({}, {}, {"token": "t"})):
            with mock.patch(
                "mutimon.main.retry_auth", return_value=({}, {}, {"token": "t2"})
            ):
                with mock.patch(
                    "mutimon.main.fetch_page",
                    side_effect=[_http_error(401), (html, locale)],
                ):
                    items = main.fetch_all_items(definition, {}, def_name="s")
        assert items[0]["t"] == "hi"

    def test_html_500_not_retried(self):
        definition = {
            "url": "https://example.com",
            "auth": {"login": {"url": "https://auth", "extract": {}}, "apply": {}},
            "query": {"type": "single", "selector": "div", "variables": {}},
        }
        with mock.patch("mutimon.main.resolve_auth", return_value=({}, {}, {"token": "t"})):
            with mock.patch("mutimon.main.fetch_page", side_effect=_http_error(500)):
                with pytest.raises(requests.HTTPError):
                    main.fetch_all_items(definition, {}, def_name="s")


# ========================= send_error_email secret resolution =========================


class TestSendErrorEmailSecrets:
    def test_resolves_secret_password(self, tmp_mutimon):
        secrets_file = tmp_mutimon / "secrets.json"
        secrets_file.write_text(json.dumps({"email": {"password": "real_pass"}}))
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "{{secret.email.password}}",
                    "email": "test@test.com",
                }
            },
            "rules": [{"email": "user@test.com"}],
        }
        config_file = tmp_mutimon / "config.json"
        config_file.write_text(json.dumps(config))
        with mock.patch.object(main, "SECRETS_FILE", str(secrets_file)):
            with mock.patch("smtplib.SMTP") as mock_smtp:
                server_instance = mock.MagicMock()
                mock_smtp.return_value.__enter__ = mock.Mock(
                    return_value=server_instance
                )
                mock_smtp.return_value.__exit__ = mock.Mock(return_value=False)
                main.send_error_email("Test", "Body")
                mock_smtp.assert_called_once_with("smtp.test.com", 587, timeout=30)
                server_instance.login.assert_called_once_with(
                    "test@test.com", "real_pass"
                )

    def test_returns_if_no_secret_file(self, tmp_mutimon):
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "{{secret.email.password}}",
                    "email": "test@test.com",
                }
            },
            "rules": [],
        }
        config_file = tmp_mutimon / "config.json"
        config_file.write_text(json.dumps(config))
        missing = str(tmp_mutimon / "nonexistent.json")
        with mock.patch.object(main, "SECRETS_FILE", missing):
            with mock.patch("smtplib.SMTP") as mock_smtp:
                main.send_error_email("Test", "Body")
                mock_smtp.assert_not_called()

    def test_returns_on_invalid_secrets_json(self, tmp_mutimon):
        secrets_file = tmp_mutimon / "secrets.json"
        secrets_file.write_text("not json")
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "{{secret.email.password}}",
                    "email": "test@test.com",
                }
            },
            "rules": [],
        }
        config_file = tmp_mutimon / "config.json"
        config_file.write_text(json.dumps(config))
        with mock.patch.object(main, "SECRETS_FILE", str(secrets_file)):
            with mock.patch("smtplib.SMTP") as mock_smtp:
                main.send_error_email("Test", "Body")
                mock_smtp.assert_not_called()
