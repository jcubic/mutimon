"""Tests for process_rule, fetch, and higher-level integration."""

import json
from datetime import datetime, timedelta
from unittest import mock

import pytest

from mutimon import main


# ========================= detect_language =========================


class TestDetectLanguage:
    def test_from_html_lang(self):
        html = '<html lang="pl"><body></body></html>'
        locale = main.detect_language(html)
        assert locale is not None

    def test_from_content_language_header(self):
        html = "<html><body></body></html>"
        locale = main.detect_language(html, response_headers={"Content-Language": "de"})
        assert locale is not None

    def test_default_en(self):
        html = "<html><body></body></html>"
        locale = main.detect_language(html)
        assert locale is not None

    def test_invalid_lang_falls_back(self):
        html = '<html lang="zzzzz"><body></body></html>'
        locale = main.detect_language(html)
        assert locale is not None


# ========================= fetch_page =========================


class TestFetchPage:
    def test_fetch_page(self):
        fake_response = mock.MagicMock()
        fake_response.text = '<html lang="en"><body>Hello</body></html>'
        fake_response.headers = {}
        with mock.patch("mutimon.main.requests.request", return_value=fake_response):
            html, locale = main.fetch_page("https://example.com")
        assert "Hello" in html

    def test_fetch_page_xml(self):
        fake_response = mock.MagicMock()
        xml_content = b"<rss><channel><item>Test</item></channel></rss>"
        fake_response.content = xml_content
        fake_response.text = xml_content.decode()
        fake_response.headers = {}
        with mock.patch("mutimon.main.requests.request", return_value=fake_response):
            html, locale = main.fetch_page("https://example.com/feed", is_xml=True)
        assert "item" in str(html)

    def test_custom_user_agent(self):
        fake_response = mock.MagicMock()
        fake_response.text = "<html></html>"
        fake_response.headers = {}
        with mock.patch("mutimon.main.requests.request", return_value=fake_response) as mock_get:
            main.fetch_page("https://example.com", user_agent="TestBot/1.0")
        call_headers = mock_get.call_args[1]["headers"]
        assert call_headers["User-Agent"] == "TestBot/1.0"


# ========================= fetch_all_items =========================


class TestFetchAllItems:
    def _mock_fetch(self, html):
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        return mock.patch("mutimon.main.requests.request", return_value=fake_resp)

    def test_basic_fetch(self):
        html = """
        <html><body>
        <div class="item" data-id="1"><h3>Title 1</h3></div>
        <div class="item" data-id="2"><h3>Title 2</h3></div>
        </body></html>
        """
        definition = {
            "url": "https://example.com",
            "query": {
                "type": "list",
                "selector": "div.item",
                "id": {"type": "attribute", "name": "data-id"},
                "variables": {
                    "title": {"selector": "h3", "value": {"type": "text"}},
                },
            },
        }
        with self._mock_fetch(html):
            items = main.fetch_all_items(definition, {})
        assert len(items) == 2
        assert items[0]["title"] == "Title 1"

    def test_expect_raises_on_missing(self):
        html = "<html><body><p>Empty</p></body></html>"
        definition = {
            "url": "https://example.com",
            "query": {
                "type": "list",
                "selector": "div.item",
                "expect": ["div.item"],
                "variables": {},
            },
        }
        with self._mock_fetch(html):
            with pytest.raises(ValueError, match="Missing expected"):
                main.fetch_all_items(definition, {})

    def test_expect_dumps_html_on_failure(self, tmp_mutimon):
        html = "<html><body><p>Cloudflare challenge</p></body></html>"
        definition = {
            "url": "https://example.com/page?q=test",
            "query": {
                "type": "list",
                "selector": "div.item",
                "expect": ["div.item"],
                "variables": {},
            },
        }
        with self._mock_fetch(html):
            with pytest.raises(ValueError, match="Missing expected"):
                main.fetch_all_items(definition, {})

        dumps_dir = tmp_mutimon / "data" / "dumps"
        dump_files = list(dumps_dir.glob("*.html"))
        assert len(dump_files) == 1
        content = dump_files[0].read_text()
        assert "Cloudflare challenge" in content

    def test_reject_returns_empty(self):
        html = """
        <html><body>
        <div class="no-results">No results found</div>
        <div class="item"><h3>Recommended</h3></div>
        </body></html>
        """
        definition = {
            "url": "https://example.com",
            "query": {
                "type": "list",
                "selector": "div.item",
                "reject": [".no-results"],
                "variables": {
                    "title": {"selector": "h3", "value": {"type": "text"}},
                },
            },
        }
        with self._mock_fetch(html):
            items = main.fetch_all_items(definition, {})
        assert len(items) == 0


# ========================= query_json =========================


class TestQueryJson:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def test_simple_path(self):
        data = {"items": [{"name": "a"}, {"name": "b"}]}
        spec = {
            "type": "list",
            "path": "items",
            "variables": {"name": {"path": "name"}},
        }
        result = main.query_json(data, spec, {})
        assert len(result) == 2
        assert result[0]["name"] == "a"

    def test_single_type(self):
        data = {"info": {"title": "Test"}}
        spec = {
            "type": "single",
            "path": "info",
            "variables": {"title": {"path": "title"}},
        }
        result = main.query_json(data, spec, {})
        assert isinstance(result, dict)
        assert result["title"] == "Test"

    def test_liquid_variables_in_path(self):
        data = {"users": {"abc": {"name": "Alice"}}}
        spec = {
            "type": "single",
            "path": "users.{{uid}}",
            "variables": {"name": {"path": "name"}},
        }
        result = main.query_json(data, spec, {"uid": "abc"})
        assert result["name"] == "Alice"


# ========================= process_rule =========================


class TestProcessRule:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def _make_config(self, tmp_mutimon):
        template = tmp_mutimon / "templates" / "test"
        template.write_text(
            "Items: {{count}}\n{% for item in items %}{{item.title}}\n{% endfor %}"
        )
        return {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "pass",
                    "email": "from@test.com",
                }
            },
            "defs": {
                "test-site": {
                    "url": "https://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "id": {"type": "attribute", "name": "data-id"},
                        "variables": {
                            "title": {
                                "selector": "h3",
                                "value": {"type": "text"},
                            },
                        },
                    },
                }
            },
            "rules": [],
        }

    def _mock_fetch(self, html):
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        return mock.patch("mutimon.main.requests.request", return_value=fake_resp)

    def test_new_items_trigger_email(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-proc",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        html = """
        <html><body>
        <div class="item" data-id="1"><h3>First</h3></div>
        <div class="item" data-id="2"><h3>Second</h3></div>
        </body></html>
        """
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_called_once()

        # State should be saved
        state = main.load_state("test-proc")
        assert len(state) == 2

    def test_known_items_no_email(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-known",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        # Pre-save state
        main.save_state(
            "test-known",
            [
                {"id": "1", "title": "First", "_valid": True},
                {"id": "2", "title": "Second", "_valid": True},
            ],
        )
        html = """
        <html><body>
        <div class="item" data-id="1"><h3>First</h3></div>
        <div class="item" data-id="2"><h3>Second</h3></div>
        </body></html>
        """
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_not_called()

    def test_save_only_mode(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-save-only",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        html = '<html><body><div class="item" data-id="1"><h3>Item</h3></div></body></html>'
        with self._mock_fetch(html):
            main.process_rule(config, rule, save_only=True)

        # Email should be saved to file
        email_file = tmp_mutimon / "data" / "emails" / "test-save-only.txt"
        assert email_file.exists()

    def test_email_failure_skips_state_save(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-fail",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        html = '<html><body><div class="item" data-id="1"><h3>Item</h3></div></body></html>'
        with self._mock_fetch(html):
            with mock.patch(
                "mutimon.main.send_email", side_effect=Exception("SMTP error")
            ):
                main.process_rule(config, rule)

        # State should NOT be saved
        state = main.load_state("test-fail")
        assert state == []

    def test_missing_definition(self, tmp_mutimon, capsys):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "nonexistent",
            "name": "test-missing",
            "subject": "Test",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        main.process_rule(config, rule)
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_dedup_across_inputs(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-dedup",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [
                {"params": {}},
                {"params": {}},
            ],
        }
        html = '<html><body><div class="item" data-id="1"><h3>Item</h3></div></body></html>'
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                # Should send only 1 item, not 2 (deduped)
                # Check the body contains the item only once
                assert mock_send.called

        state = main.load_state("test-dedup")
        assert len(state) == 1  # Deduped

    def test_dedupe_by_fields(self, tmp_mutimon):
        """dedupe option removes items with same field combination."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-dedupe-fields",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "dedupe": ["title"],
        }
        html = """
        <html><body>
        <div class="item" data-id="1"><h3>Same Title</h3></div>
        <div class="item" data-id="2"><h3>Same Title</h3></div>
        <div class="item" data-id="3"><h3>Different</h3></div>
        </body></html>
        """
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_called_once()

        state = main.load_state("test-dedupe-fields")
        assert len(state) == 2
        titles = [s["title"] for s in state]
        assert "Same Title" in titles
        assert "Different" in titles

    def test_dedupe_by_multiple_fields(self, tmp_mutimon):
        """dedupe with multiple fields uses composite key."""
        config = self._make_config(tmp_mutimon)
        # Add company variable to the definition
        config["defs"]["test-site"]["query"]["variables"]["company"] = {
            "selector": "span.company",
            "value": {"type": "text"},
        }
        rule = {
            "ref": "test-site",
            "name": "test-dedupe-multi",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "dedupe": ["title", "company"],
        }
        html = """
        <html><body>
        <div class="item" data-id="1"><h3>Dev</h3><span class="company">Acme</span></div>
        <div class="item" data-id="2"><h3>Dev</h3><span class="company">Acme</span></div>
        <div class="item" data-id="3"><h3>Dev</h3><span class="company">Other</span></div>
        </body></html>
        """
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        state = main.load_state("test-dedupe-multi")
        assert len(state) == 2

    def test_dedupe_not_set_keeps_all(self, tmp_mutimon):
        """Without dedupe, items with same fields but different IDs are kept."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-no-dedupe",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        html = """
        <html><body>
        <div class="item" data-id="1"><h3>Same</h3></div>
        <div class="item" data-id="2"><h3>Same</h3></div>
        </body></html>
        """
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        state = main.load_state("test-no-dedupe")
        assert len(state) == 2


# ========================= init mode =========================


class TestInitMode:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def _make_config(self, tmp_mutimon):
        template = tmp_mutimon / "templates" / "test"
        template.write_text(
            "Items: {{count}}\n{% for item in items %}{{item.title}}\n{% endfor %}"
        )
        return {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "pass",
                    "email": "from@test.com",
                }
            },
            "defs": {
                "test-site": {
                    "url": "https://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "id": {"type": "attribute", "name": "data-id"},
                        "variables": {
                            "title": {
                                "selector": "h3",
                                "value": {"type": "text"},
                            },
                        },
                    },
                }
            },
            "rules": [],
        }

    def _mock_fetch(self, html):
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        return mock.patch("mutimon.main.requests.request", return_value=fake_resp)

    def test_init_saves_state_without_email(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-init",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        html = """
        <html><body>
        <div class="item" data-id="1"><h3>First</h3></div>
        <div class="item" data-id="2"><h3>Second</h3></div>
        </body></html>
        """
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule, init=True)
                mock_send.assert_not_called()

        state = main.load_state("test-init")
        assert len(state) == 2

    def test_init_cli_flag(self, tmp_mutimon, write_config, sample_config):
        write_config()
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        html = '<html><body><div class="item"><h3>X</h3><a href="/x">x</a></div></body></html>'
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        with mock.patch("sys.argv", ["mon", "--init", "test-rule"]):
            with mock.patch("mutimon.main.requests.request", return_value=fake_resp):
                with mock.patch("mutimon.main.send_email") as mock_send:
                    main.run()
                    mock_send.assert_not_called()

        state = main.load_state("test-rule")
        assert len(state) > 0


# ========================= enabled flag =========================


class TestEnabledFlag:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def test_disabled_rule_skipped(self, tmp_mutimon, write_config, sample_config):
        sample_config["rules"][0]["enabled"] = False
        write_config(sample_config)
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        with mock.patch("sys.argv", ["mon", "--force"]):
            with mock.patch("mutimon.main.requests.request") as mock_req:
                with mock.patch("mutimon.main.send_email"):
                    main.run()
                    mock_req.assert_not_called()

    def test_enabled_true_runs(self, tmp_mutimon, write_config, sample_config):
        sample_config["rules"][0]["enabled"] = True
        write_config(sample_config)
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        html = '<html><body><div class="item"><h3>X</h3><a href="/x">x</a></div></body></html>'
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        with mock.patch("sys.argv", ["mon", "--force"]):
            with mock.patch("mutimon.main.requests.request", return_value=fake_resp):
                with mock.patch("mutimon.main.send_email"):
                    main.run()

        state = main.load_state("test-rule")
        assert len(state) > 0


# ========================= per-rule logging =========================


class TestRuleLogging:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def _make_config(self, tmp_mutimon):
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        return {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "pass",
                    "email": "from@test.com",
                }
            },
            "defs": {
                "test-site": {
                    "url": "https://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "id": {"type": "attribute", "name": "data-id"},
                        "variables": {
                            "title": {
                                "selector": "h3",
                                "value": {"type": "text"},
                            },
                        },
                    },
                }
            },
            "rules": [],
        }

    def _mock_fetch(self, html):
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        return mock.patch("mutimon.main.requests.request", return_value=fake_resp)

    def test_log_creates_file(self, tmp_mutimon, monkeypatch):
        monkeypatch.setattr(main, "LOGS_DIR", str(tmp_mutimon / "logs"))
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-log",
            "log": True,
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        html = '<html><body><div class="item" data-id="1"><h3>Item</h3></div></body></html>'
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        log_file = tmp_mutimon / "logs" / "test-log.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "RUN START" in content
        assert "RUN END" in content

    def test_log_false_no_file(self, tmp_mutimon, monkeypatch):
        monkeypatch.setattr(main, "LOGS_DIR", str(tmp_mutimon / "logs"))
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-nolog",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        html = '<html><body><div class="item" data-id="1"><h3>Item</h3></div></body></html>'
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        log_file = tmp_mutimon / "logs" / "test-nolog.log"
        assert not log_file.exists()


# ========================= state retention =========================


class TestStateRetention:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def _make_config(self, tmp_mutimon):
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        return {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "pass",
                    "email": "from@test.com",
                }
            },
            "defs": {
                "test-site": {
                    "url": "https://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "id": {"type": "attribute", "name": "data-id"},
                        "variables": {
                            "title": {
                                "selector": "h3",
                                "value": {"type": "text"},
                            },
                        },
                    },
                }
            },
            "rules": [],
        }

    def _mock_fetch(self, html):
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        return mock.patch("mutimon.main.requests.request", return_value=fake_resp)

    def test_disappeared_items_retained(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-retain",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        recent = datetime.now().isoformat()
        main.save_state("test-retain", [
            {"id": "1", "title": "Stays", "_valid": True, "_last_seen": recent},
            {"id": "2", "title": "Gone", "_valid": True, "_last_seen": recent},
        ])
        html = '<html><body><div class="item" data-id="1"><h3>Stays</h3></div></body></html>'
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        state = main.load_state("test-retain")
        ids = {item["id"] for item in state}
        assert "1" in ids
        assert "2" in ids

    def test_old_items_pruned(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-prune",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        old = (datetime.now() - timedelta(days=60)).isoformat()
        main.save_state("test-prune", [
            {"id": "1", "title": "Current", "_valid": True, "_last_seen": old},
            {"id": "2", "title": "Expired", "_valid": True, "_last_seen": old},
        ])
        html = '<html><body><div class="item" data-id="1"><h3>Current</h3></div></body></html>'
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        state = main.load_state("test-prune")
        ids = {item["id"] for item in state}
        assert "1" in ids
        assert "2" not in ids

    def test_returning_item_no_duplicate_notification(self, tmp_mutimon):
        """Item disappears and reappears — should NOT trigger notification."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-return",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        recent = datetime.now().isoformat()
        main.save_state("test-return", [
            {"id": "1", "title": "First", "_valid": True, "_last_seen": recent},
            {"id": "2", "title": "Second", "_valid": True, "_last_seen": recent},
        ])
        html = """
        <html><body>
        <div class="item" data-id="1"><h3>First</h3></div>
        <div class="item" data-id="2"><h3>Second</h3></div>
        </body></html>
        """
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_not_called()

    def test_items_without_last_seen_retained(self, tmp_mutimon):
        """Legacy items (no _last_seen) should be retained, not pruned."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-legacy",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        main.save_state("test-legacy", [
            {"id": "1", "title": "Current", "_valid": True},
            {"id": "2", "title": "Legacy no timestamp", "_valid": True},
        ])
        html = '<html><body><div class="item" data-id="1"><h3>Current</h3></div></body></html>'
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        state = main.load_state("test-legacy")
        ids = {item["id"] for item in state}
        assert "2" in ids

    def test_last_seen_updated_on_fetch(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-seen",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        html = '<html><body><div class="item" data-id="1"><h3>Item</h3></div></body></html>'
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        state = main.load_state("test-seen")
        assert len(state) == 1
        assert "_last_seen" in state[0]


# ========================= undefined variable error =========================


class TestUndefinedVariableError:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def test_raises_on_missing_variable(self):
        item = {"other_field": 42}
        with pytest.raises(main.UndefinedVariableError, match="price"):
            main.evaluate_single_validator(
                {"test": "{{price}} > 10"}, item
            )

    def test_passes_with_present_variable(self):
        item = {"price": 100.0}
        assert main.evaluate_single_validator(
            {"test": "{{price}} > 10"}, item
        ) is True

    def test_process_rule_sends_error_email(self, tmp_mutimon):
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "pass",
                    "email": "from@test.com",
                }
            },
            "defs": {
                "test-site": {
                    "url": "https://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "id": {"type": "attribute", "name": "data-id"},
                        "variables": {
                            "title": {
                                "selector": "h3",
                                "value": {"type": "text"},
                            },
                        },
                    },
                }
            },
            "rules": [],
        }
        rule = {
            "ref": "test-site",
            "name": "test-undef",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [
                {
                    "params": {},
                    "validator": {"test": "{{missing_var}} > 0"},
                }
            ],
        }
        html = '<html><body><div class="item" data-id="1"><h3>Item</h3></div></body></html>'
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        with mock.patch("mutimon.main.requests.request", return_value=fake_resp):
            with mock.patch("mutimon.main.send_error_email") as mock_err:
                main.process_rule(config, rule)
                mock_err.assert_called_once()
                assert "missing_var" in mock_err.call_args[0][1]

        state = main.load_state("test-undef")
        assert state == []


# ========================= resolve_inputs with list params =========================


class TestResolveInputsListParams:
    def test_list_params_with_input_validator(self):
        rule = {
            "params": [
                {"website_id": "abc", "site_name": "Site1"},
                {"website_id": "def", "site_name": "Site2"},
            ],
            "input": {
                "validator": {"test": "{{pageviews}} > 0"},
            },
        }
        result = main.resolve_inputs(rule)
        assert len(result) == 2
        assert result[0]["params"] == {"website_id": "abc", "site_name": "Site1"}
        assert result[1]["params"] == {"website_id": "def", "site_name": "Site2"}
        assert result[0]["validator"] is not None
        assert result[1]["validator"] is not None

    def test_list_params_without_input(self):
        rule = {
            "params": [
                {"q": "python"},
                {"q": "rust"},
            ],
        }
        result = main.resolve_inputs(rule)
        assert len(result) == 2
        assert result[0]["params"] == {"q": "python"}
        assert result[1]["params"] == {"q": "rust"}

    def test_input_entry_with_own_params_not_expanded(self):
        rule = {
            "params": [{"q": "a"}, {"q": "b"}],
            "input": [
                {"params": {"q": "override"}},
            ],
        }
        result = main.resolve_inputs(rule)
        assert len(result) == 1
        assert result[0]["params"] == {"q": "override"}


# ========================= send_error_email =========================


class TestSendErrorEmail:
    def test_sends_error_email(self, tmp_mutimon, write_config, sample_config):
        write_config()
        with mock.patch("mutimon.main.smtplib.SMTP") as mock_smtp:
            mock_server = mock.MagicMock()
            mock_smtp.return_value.__enter__ = mock.Mock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = mock.Mock(return_value=False)
            main.send_error_email("[mutimon] Test error", "Error details")
            mock_server.send_message.assert_called_once()

    def test_error_email_catches_exceptions(self, tmp_mutimon, write_config, sample_config):
        write_config()
        with mock.patch("mutimon.main.smtplib.SMTP", side_effect=Exception("fail")):
            # Should not raise, just print
            main.send_error_email("[mutimon] Test", "Body")


# ========================= run() CLI handler =========================


class TestRunFunction:
    def setup_method(self):
        main.verbose = False

    def test_validate_flag(self, tmp_mutimon, write_config, sample_config):
        write_config()
        with mock.patch("sys.argv", ["mon", "--validate"]):
            main.run()  # Should not raise

    def test_list_flag(self, tmp_mutimon, write_config, sample_config, capsys):
        write_config()
        with mock.patch("sys.argv", ["mon", "--list"]):
            main.run()
        out = capsys.readouterr().out
        assert "test-rule" in out

    def test_list_empty(self, tmp_mutimon, write_config, capsys):
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "x",
                    "email": "x@x.com",
                }
            },
            "defs": {},
            "rules": [],
        }
        write_config(config)
        with mock.patch("sys.argv", ["mon", "--list"]):
            main.run()
        out = capsys.readouterr().out
        assert "No rules" in out

    def test_ai_guide_flag(self, capsys):
        with mock.patch("sys.argv", ["mon", "--ai-guide"]):
            main.run()
        out = capsys.readouterr().out
        assert "# Mutimon" in out

    def test_cron_default(self, capsys):
        with mock.patch("sys.argv", ["mon", "--cron"]):
            main.run()
        out = capsys.readouterr().out
        assert "*/5 * * * *" in out
        assert "mon -q" in out
        assert "mutimon.log" in out

    def test_cron_custom_schedule(self, capsys):
        with mock.patch("sys.argv", ["mon", "--cron", "0 8 * * *"]):
            main.run()
        out = capsys.readouterr().out
        assert "0 8 * * *" in out

    def test_cron_fallback_to_argv(self, capsys):
        with mock.patch("sys.argv", ["mon", "--cron"]):
            with mock.patch("shutil.which", return_value=None):
                main.run()
        out = capsys.readouterr().out
        assert "mon -q" in out

    def test_skeleton_email_rejected(self, tmp_mutimon, write_config):
        config = {
            "email": {
                "server": {
                    "host": "smtp.example.com",
                    "port": 587,
                    "password": "your-password-here",
                    "email": "you@example.com",
                }
            },
            "defs": {},
            "rules": [],
        }
        write_config(config)
        with mock.patch("sys.argv", ["mon"]):
            with mock.patch.object(main, "load_secrets", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    main.run()
                assert exc.value.code == 1

    def test_force_specific_rule(self, tmp_mutimon, write_config, sample_config):
        write_config()
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        html = '<html><body><div class="item"><h3>X</h3><a href="/x">x</a></div></body></html>'
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        with mock.patch("sys.argv", ["mon", "--force", "test-rule"]):
            with mock.patch("mutimon.main.requests.request", return_value=fake_resp):
                with mock.patch("mutimon.main.send_email"):
                    main.run()

    def test_force_multiple_rules(self, tmp_mutimon, write_config, sample_config):
        sample_config["rules"].append({
            "ref": "test-site",
            "name": "test-rule-2",
            "schedule": "0 * * * *",
            "subject": "Test 2: {{count}} items",
            "template": "./templates/test",
            "email": "user@test.com",
        })
        write_config(sample_config)
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        html = '<html><body><div class="item"><h3>X</h3><a href="/x">x</a></div></body></html>'
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        with mock.patch("sys.argv", ["mon", "--force", "test-rule", "test-rule-2"]):
            with mock.patch("mutimon.main.requests.request", return_value=fake_resp):
                with mock.patch("mutimon.main.send_email"):
                    main.run()
        assert len(main.load_state("test-rule")) > 0
        assert len(main.load_state("test-rule-2")) > 0

    def test_force_unknown_rule_exits(self, tmp_mutimon, write_config, sample_config):
        write_config()
        with mock.patch("sys.argv", ["mon", "--force", "nonexistent"]):
            with pytest.raises(SystemExit):
                main.run()

    def test_force_one_unknown_rule_exits(self, tmp_mutimon, write_config, sample_config):
        write_config()
        with mock.patch("sys.argv", ["mon", "--force", "test-rule", "nonexistent"]):
            with pytest.raises(SystemExit):
                main.run()

    def test_quiet_suppresses_output(self, tmp_mutimon, write_config, sample_config, capsys):
        write_config()
        with mock.patch("sys.argv", ["mon", "--quiet", "--validate"]):
            main.run()
        out = capsys.readouterr().out
        # quiet suppresses stdout
        assert out == ""

    def test_dry_run(self, tmp_mutimon, write_config, sample_config):
        write_config()
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        html = '<html><body><div class="item"><h3>X</h3><a href="/x">x</a></div></body></html>'
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        with mock.patch("sys.argv", ["mon", "--dry-run", "--force"]):
            with mock.patch("mutimon.main.requests.request", return_value=fake_resp):
                main.run()
        # No state saved in dry-run
        state = main.load_state("test-rule")
        assert state == []


# ========================= flatten =========================


class TestFlatten:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def _make_config(self, tmp_mutimon, url="https://example.com/{{page}}"):
        template = tmp_mutimon / "templates" / "test"
        template.write_text(
            "{% for group in items %}"
            "[{{ group | first | map: '_input' | first }}]"
            "{% for item in group %}{{item.title}},{% endfor %}"
            "{% endfor %}"
        )
        return {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "pass",
                    "email": "from@test.com",
                }
            },
            "defs": {
                "test-site": {
                    "url": url,
                    "params": ["page"],
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "id": {"type": "attribute", "name": "data-id"},
                        "variables": {
                            "title": {
                                "selector": "h3",
                                "value": {"type": "text"},
                            },
                        },
                    },
                }
            },
            "rules": [],
        }

    def _mock_fetch_pages(self, pages):
        """Mock that returns different HTML based on the URL's page param."""
        def side_effect(*args, **kwargs):
            url = args[1] if len(args) > 1 else kwargs.get("url", "")
            for key, html in pages.items():
                if key in url:
                    resp = mock.MagicMock()
                    resp.text = html
                    resp.headers = {}
                    return resp
            resp = mock.MagicMock()
            resp.text = "<html><body></body></html>"
            resp.headers = {}
            return resp
        return mock.patch("mutimon.main.requests.request", side_effect=side_effect)

    def test_flatten_false_groups_items_by_input(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-flatten",
            "flatten": False,
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [
                {"params": {"page": "SEO"}},
                {"params": {"page": "UKEN"}},
            ],
        }
        pages = {
            "SEO": '<html><body><div class="item" data-id="1"><h3>A</h3></div></body></html>',
            "UKEN": '<html><body><div class="item" data-id="2"><h3>B</h3></div></body></html>',
        }
        with self._mock_fetch_pages(pages):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_called_once()
                body = mock_send.call_args[0][3]
                assert "A," in body
                assert "B," in body

    def test_flatten_false_second_group_only(self, tmp_mutimon):
        """When first input has no new items, second group gets correct metadata."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-flatten-second",
            "flatten": False,
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [
                {"params": {"page": "SEO"}},
                {"params": {"page": "UKEN"}},
            ],
        }
        # Pre-save SEO item so it's not "new"
        main.save_state("test-flatten-second", [
            {"id": "1", "title": "A", "_valid": True,
             "_last_seen": "2099-01-01T00:00:00"},
        ])
        pages = {
            "SEO": '<html><body><div class="item" data-id="1"><h3>A</h3></div></body></html>',
            "UKEN": '<html><body><div class="item" data-id="2"><h3>B</h3></div></body></html>',
        }
        # Use a template that shows _input.page
        template = tmp_mutimon / "templates" / "test"
        template.write_text(
            "{% for group in items %}"
            "{% assign first = group | first %}"
            "PAGE:{{ first._input.page }}:"
            "{% for item in group %}{{item.title}},{% endfor %}"
            "{% endfor %}"
        )
        with self._mock_fetch_pages(pages):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_called_once()
                body = mock_send.call_args[0][3]
                assert "PAGE:UKEN:" in body
                assert "PAGE:SEO:" not in body

    def test_flatten_false_no_dedup(self, tmp_mutimon):
        """With flatten=false, same ID from different inputs is kept in both groups."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-no-dedup",
            "flatten": False,
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [
                {"params": {"page": "SEO"}},
                {"params": {"page": "UKEN"}},
            ],
        }
        same_html = '<html><body><div class="item" data-id="1"><h3>Item</h3></div></body></html>'
        pages = {"SEO": same_html, "UKEN": same_html}
        with self._mock_fetch_pages(pages):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)
        state = main.load_state("test-no-dedup")
        assert len(state) == 2

    def test_flatten_true_deduplicates(self, tmp_mutimon):
        """With flatten=true (default), duplicate IDs are deduplicated."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-dedup-flat",
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [
                {"params": {"page": "SEO"}},
                {"params": {"page": "UKEN"}},
            ],
        }
        # Use flat template
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{% for item in items %}{{item.title}},{% endfor %}")
        same_html = '<html><body><div class="item" data-id="1"><h3>Item</h3></div></body></html>'
        pages = {"SEO": same_html, "UKEN": same_html}
        with self._mock_fetch_pages(pages):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)
        state = main.load_state("test-dedup-flat")
        assert len(state) == 1

    def test_flatten_false_single_input_renders_flat(self, tmp_mutimon):
        """With flatten=false but only one input, renders as flat list."""
        config = self._make_config(tmp_mutimon)
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{% for item in items %}{{item.title}},{% endfor %}")
        rule = {
            "ref": "test-site",
            "name": "test-flatten-single",
            "flatten": False,
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [{"params": {"page": "SEO"}}],
        }
        pages = {
            "SEO": '<html><body><div class="item" data-id="1"><h3>X</h3></div></body></html>',
        }
        with self._mock_fetch_pages(pages):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_called_once()
                body = mock_send.call_args[0][3]
                assert "X," in body


# ========================= aggregated rule (per-input ref) =========================


class TestAggregatedRule:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def _make_config(self, tmp_mutimon):
        template = tmp_mutimon / "templates" / "test"
        template.write_text(
            "{% for group in items %}"
            "[{{group[0]._label}}]"
            "{% for item in group %}{{item.title}},{% endfor %}"
            "{% endfor %}"
        )
        return {
            "email": {
                "server": {
                    "host": "smtp.test.com", "port": 587,
                    "password": "pass", "email": "from@test.com",
                }
            },
            "defs": {
                "site-a": {
                    "url": "https://a.example.com",
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "id": {"type": "attribute", "name": "data-id"},
                        "variables": {
                            "title": {"selector": "h3", "value": {"type": "text"}},
                        },
                    },
                },
                "site-b": {
                    "url": "https://b.example.com",
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "id": {"type": "attribute", "name": "data-id"},
                        "variables": {
                            "title": {"selector": "h3", "value": {"type": "text"}},
                        },
                    },
                },
            },
            "rules": [],
        }

    def _mock_fetch_pages(self, pages):
        """Mock that returns different HTML based on URL substring."""
        def side_effect(*args, **kwargs):
            url = args[1] if len(args) > 1 else kwargs.get("url", "")
            for key, html in pages.items():
                if key in url:
                    resp = mock.MagicMock()
                    resp.text = html
                    resp.headers = {}
                    return resp
            resp = mock.MagicMock()
            resp.text = "<html><body></body></html>"
            resp.headers = {}
            return resp
        return mock.patch("mutimon.main.requests.request", side_effect=side_effect)

    def test_aggregates_multiple_refs(self, tmp_mutimon):
        """Rule with per-input ref fetches from multiple defs into one email."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "name": "agg-rule",
            "flatten": False,
            "subject": "Agg: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [
                {"ref": "site-a", "label": "A", "params": {}},
                {"ref": "site-b", "label": "B", "params": {}},
            ],
        }
        pages = {
            "a.example.com": '<html><body><div class="item" data-id="1"><h3>Alpha</h3></div></body></html>',
            "b.example.com": '<html><body><div class="item" data-id="1"><h3>Beta</h3></div></body></html>',
        }
        with self._mock_fetch_pages(pages):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_called_once()
                body = mock_send.call_args[0][3]
                assert "[A]" in body and "Alpha," in body
                assert "[B]" in body and "Beta," in body

    def test_id_namespacing_prevents_collision(self, tmp_mutimon):
        """When two defs produce the same ID, IDs are namespaced by ref."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "name": "agg-id-test",
            "subject": "x",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [
                {"ref": "site-a", "label": "A", "params": {}},
                {"ref": "site-b", "label": "B", "params": {}},
            ],
        }
        # Both pages produce data-id="1" — without namespacing they'd dedupe to 1 item
        pages = {
            "a.example.com": '<html><body><div class="item" data-id="1"><h3>Alpha</h3></div></body></html>',
            "b.example.com": '<html><body><div class="item" data-id="1"><h3>Beta</h3></div></body></html>',
        }
        with self._mock_fetch_pages(pages):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)
        state = main.load_state("agg-id-test")
        assert len(state) == 2
        ids = sorted(s["id"] for s in state)
        assert ids == ["site-a:1", "site-b:1"]

    def test_label_defaults_to_ref(self, tmp_mutimon):
        """When no label is set, the ref name is used."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "name": "agg-no-label",
            "flatten": False,
            "subject": "x",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [
                {"ref": "site-a", "params": {}},
            ],
        }
        pages = {
            "a.example.com": '<html><body><div class="item" data-id="1"><h3>X</h3></div></body></html>',
        }
        with self._mock_fetch_pages(pages):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)
        state = main.load_state("agg-no-label")
        assert state[0]["_label"] == "site-a"

    def test_empty_groups_filtered_in_template(self, tmp_mutimon):
        """Groups with no items don't appear in template (existing behavior)."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "name": "agg-empty-groups",
            "flatten": False,
            "subject": "x",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [
                {"ref": "site-a", "label": "A", "params": {}},
                {"ref": "site-b", "label": "B", "params": {}},
            ],
        }
        pages = {
            "a.example.com": '<html><body><div class="item" data-id="1"><h3>Alpha</h3></div></body></html>',
            # site-b returns nothing
        }
        with self._mock_fetch_pages(pages):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                body = mock_send.call_args[0][3]
                assert "[A]" in body
                assert "[B]" not in body

    def test_top_level_ref_still_works(self, tmp_mutimon):
        """Existing rules with top-level ref keep working unchanged."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "site-a",
            "name": "agg-fallback",
            "subject": "x",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        pages = {
            "a.example.com": '<html><body><div class="item" data-id="1"><h3>X</h3></div></body></html>',
        }
        with self._mock_fetch_pages(pages):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_called_once()
        state = main.load_state("agg-fallback")
        # No namespacing because no input has its own ref
        assert state[0]["id"] == "1"

    def test_no_ref_anywhere_errors(self, tmp_mutimon, capsys):
        """Rule with neither top-level ref nor any input ref reports error."""
        config = self._make_config(tmp_mutimon)
        rule = {
            "name": "agg-no-ref",
            "subject": "x",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [{"params": {}}],
        }
        main.process_rule(config, rule)
        err = capsys.readouterr().err
        assert "no 'ref'" in err


# ========================= rule logging details =========================


class TestRuleLoggingDetails:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def _make_config(self, tmp_mutimon):
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        return {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "pass",
                    "email": "from@test.com",
                }
            },
            "defs": {
                "test-site": {
                    "url": "https://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "id": {"type": "attribute", "name": "data-id"},
                        "variables": {
                            "title": {
                                "selector": "h3",
                                "value": {"type": "text"},
                            },
                        },
                    },
                }
            },
            "rules": [],
        }

    def _mock_fetch(self, html):
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        return mock.patch("mutimon.main.requests.request", return_value=fake_resp)

    def test_log_disappeared_ids(self, tmp_mutimon, monkeypatch):
        monkeypatch.setattr(main, "LOGS_DIR", str(tmp_mutimon / "logs"))
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-disappeared",
            "log": True,
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        main.save_state("test-disappeared", [
            {"id": "1", "title": "Stays", "_valid": True,
             "_last_seen": "2099-01-01T00:00:00"},
            {"id": "99", "title": "Gone", "_valid": True,
             "_last_seen": "2099-01-01T00:00:00"},
        ])
        html = '<html><body><div class="item" data-id="1"><h3>Stays</h3></div></body></html>'
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        log_file = tmp_mutimon / "logs" / "test-disappeared.log"
        content = log_file.read_text()
        assert "Disappeared IDs" in content
        assert "99" in content

    def test_log_threshold_crossed(self, tmp_mutimon, monkeypatch):
        monkeypatch.setattr(main, "LOGS_DIR", str(tmp_mutimon / "logs"))
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "test-site",
            "name": "test-threshold-log",
            "log": True,
            "subject": "New: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": [{
                "params": {},
                "validator": {"test": "1 == 1"},
            }],
        }
        main.save_state("test-threshold-log", [
            {"id": "1", "title": "Item", "_valid": False,
             "_last_seen": "2099-01-01T00:00:00"},
        ])
        html = '<html><body><div class="item" data-id="1"><h3>Item</h3></div></body></html>'
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        log_file = tmp_mutimon / "logs" / "test-threshold-log.log"
        content = log_file.read_text()
        assert "threshold crossed" in content


# ========================= completion and completer =========================


class TestCompletion:
    def test_completion_flag(self, tmp_mutimon, write_config, sample_config):
        write_config()
        with mock.patch("sys.argv", ["mon", "--completion", "bash"]):
            with mock.patch("mutimon.main.init_config"):
                main.run()

    def test_completion_outputs_shellcode(self, tmp_mutimon, write_config,
                                         sample_config, capsys):
        write_config()
        with mock.patch("sys.argv", ["mon", "--completion", "bash"]):
            main.run()
        out = capsys.readouterr().out
        assert len(out) > 0


class TestRuleNameCompleter:
    def test_returns_rule_names(self, tmp_mutimon):
        config = {
            "rules": [
                {"name": "rule-alpha"},
                {"name": "rule-beta"},
            ]
        }
        config_file = tmp_mutimon / "config.json"
        config_file.write_text(json.dumps(config))
        completer = main.RuleNameCompleter()
        result = completer()
        assert "rule-alpha" in result
        assert "rule-beta" in result

    def test_returns_empty_on_missing_config(self, tmp_mutimon):
        completer = main.RuleNameCompleter()
        result = completer()
        assert result == []

    def test_returns_empty_on_invalid_json(self, tmp_mutimon):
        config_file = tmp_mutimon / "config.json"
        config_file.write_text("not json")
        completer = main.RuleNameCompleter()
        result = completer()
        assert result == []


class TestHealthCheck:
    """Tests for definitions without query (health check mode)."""

    def _mock_response(self, status_code=200, body="OK", headers=None):
        resp = mock.MagicMock()
        resp.status_code = status_code
        resp.text = body
        resp.headers = headers or {"content-type": "text/html", "server": "nginx"}
        resp.raise_for_status = mock.MagicMock()
        if status_code >= 400:
            from requests.exceptions import HTTPError
            resp.raise_for_status.side_effect = HTTPError(response=resp)
        return resp

    def test_health_check_returns_item_with_http_metadata(self):
        definition = {"url": "https://example.com"}
        resp = self._mock_response(200, "OK", {"content-type": "text/html"})
        with mock.patch("mutimon.main.requests.request", return_value=resp):
            items = main.fetch_all_items(definition, {})
        assert len(items) == 1
        item = items[0]
        assert item["id"] == "https://example.com"
        assert item["url"] == "https://example.com"
        assert item["http"]["code"] == 200
        assert item["http"]["method"] == "GET"
        assert item["http"]["body"] == "OK"
        assert item["http"]["headers"]["content-type"] == "text/html"
        assert item["http"]["error"] is None

    def test_health_check_with_params(self):
        definition = {"url": "https://{{ host }}/status"}
        resp = self._mock_response(200)
        with mock.patch("mutimon.main.requests.request", return_value=resp):
            items = main.fetch_all_items(definition, {"host": "example.com"})
        assert len(items) == 1
        assert items[0]["url"] == "https://example.com/status"

    def test_health_check_connection_error(self):
        definition = {"url": "https://example.com"}
        with mock.patch(
            "mutimon.main.requests.request",
            side_effect=Exception("Connection refused"),
        ):
            items = main.fetch_all_items(definition, {})
        assert len(items) == 1
        item = items[0]
        assert item["http"]["code"] == 0
        assert item["http"]["error"] == "Connection refused"
        assert item["http"]["headers"] == {}
        assert item["http"]["body"] == ""

    def test_health_check_http_error(self):
        definition = {"url": "https://example.com"}
        resp = self._mock_response(503, "Service Unavailable")
        with mock.patch("mutimon.main.requests.request", return_value=resp):
            items = main.fetch_all_items(definition, {})
        assert len(items) == 1
        assert items[0]["http"]["code"] == 503

    def test_health_check_headers_lowercase(self):
        definition = {"url": "https://example.com"}
        resp = self._mock_response(200, "OK", {"Content-Type": "text/html", "X-Custom": "value"})
        with mock.patch("mutimon.main.requests.request", return_value=resp):
            items = main.fetch_all_items(definition, {})
        headers = items[0]["http"]["headers"]
        assert "content-type" in headers
        assert "x-custom" in headers
        assert headers["content-type"] == "text/html"

    def test_health_check_custom_method(self):
        definition = {"url": "https://example.com", "method": "HEAD"}
        resp = self._mock_response(200, "")
        with mock.patch("mutimon.main.requests.request", return_value=resp) as mock_req:
            main.fetch_all_items(definition, {})
        assert mock_req.call_args[0][0] == "HEAD"

    def test_health_check_response_time(self):
        definition = {"url": "https://example.com"}
        resp = self._mock_response(200)
        with mock.patch("mutimon.main.requests.request", return_value=resp):
            items = main.fetch_all_items(definition, {})
        assert "response_time" in items[0]["http"]
        assert isinstance(items[0]["http"]["response_time"], float)

    def test_health_check_with_track(self, tmp_mutimon):
        """Health check integrated with track for up/down state machine."""
        definition = {"url": "https://example.com"}
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "email": "test@test.com",
                    "password": "pass",
                }
            },
            "defs": {"health": definition},
            "rules": [
                {
                    "ref": "health",
                    "name": "test-health",
                    "schedule": "* * * * *",
                    "subject": "Site down",
                    "template": "./templates/test",
                    "email": "user@test.com",
                    "input": [
                        {
                            "params": {"url": "https://example.com"},
                            "track": {
                                "states": [
                                    {
                                        "name": "down",
                                        "test": "({{ http.code }} >= 400) | ({{ http.code }} == 0)",
                                    },
                                    {"name": "up", "test": "{{ http.code }} >= 200", "silent": True},
                                ]
                            },
                        }
                    ],
                }
            ],
        }
        template_file = tmp_mutimon / "templates" / "test"
        template_file.write_text("{% for item in items %}{{ item.url }} is {{ item._state_name }}{% endfor %}")

        resp = self._mock_response(200)
        with (
            mock.patch("mutimon.main.requests.request", return_value=resp),
            mock.patch("mutimon.main.send_email") as mock_send,
        ):
            main.process_rule(config, config["rules"][0])

        # First run with status 200 -> "up" state is silent, no email
        mock_send.assert_not_called()

    def test_health_check_process_rule_no_query(self, tmp_mutimon):
        """process_rule works when definition has no query."""
        definition = {"url": "https://{{ url }}"}
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "email": "test@test.com",
                    "password": "pass",
                }
            },
            "defs": {"health": definition},
            "rules": [
                {
                    "ref": "health",
                    "name": "test-health",
                    "schedule": "* * * * *",
                    "subject": "Health: {{ url }}",
                    "template": "./templates/test",
                    "email": "user@test.com",
                    "params": {"url": "example.com"},
                }
            ],
        }
        template_file = tmp_mutimon / "templates" / "test"
        template_file.write_text("{{ http.code }}")

        resp = self._mock_response(200)
        with (
            mock.patch("mutimon.main.requests.request", return_value=resp),
            mock.patch("mutimon.main.send_email") as mock_send,
        ):
            main.process_rule(config, config["rules"][0])

        # New item with status 200 — should send email
        mock_send.assert_called_once()


class TestResolveVar:
    """Tests for nested variable resolution in validators."""

    def test_simple_key(self):
        item = {"title": "Hello"}
        assert main._resolve_var(item, "title") == "Hello"

    def test_nested_key(self):
        item = {"http": {"code": 200}}
        assert main._resolve_var(item, "http.code") == 200

    def test_deeply_nested(self):
        item = {"http": {"headers": {"content-type": "text/html"}}}
        assert main._resolve_var(item, "http.headers.content-type") == "text/html"

    def test_missing_key_returns_empty(self):
        item = {"http": {"code": 200}}
        assert main._resolve_var(item, "http.missing") == ""

    def test_missing_top_level_returns_empty(self):
        item = {"title": "Hello"}
        assert main._resolve_var(item, "missing") == ""

    def test_non_dict_intermediate_returns_empty(self):
        item = {"title": "Hello"}
        assert main._resolve_var(item, "title.sub") == ""


class TestMainEntryPoint:
    def test_main_catches_exceptions(self, capsys):
        with mock.patch("mutimon.main.run", side_effect=Exception("boom")):
            with mock.patch("mutimon.main.send_error_email"):
                main.main()
        err = capsys.readouterr().err
        assert "boom" in err
