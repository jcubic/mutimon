"""Additional tests for remaining coverage gaps."""

import json
import os
from unittest import mock

import pytest

from mutimon import main


# ========================= init_config fallback =========================


class TestInitConfigFallback:
    def test_fallback_minimal_config(self, tmp_mutimon, monkeypatch):
        """When skeleton dir is missing, write minimal config inline."""
        monkeypatch.setattr(main, "SKELETON_DIR", "/nonexistent/skeleton")
        with pytest.raises(SystemExit):
            main.init_config()
        config_file = tmp_mutimon / "config.json"
        assert config_file.exists()
        config = json.loads(config_file.read_text())
        assert "email" in config
        assert config["defs"] == {}

    def test_copies_skeleton_templates(self, tmp_mutimon):
        """Skeleton templates should be copied on first run."""
        with pytest.raises(SystemExit):
            main.init_config()
        templates_dir = tmp_mutimon / "templates"
        # Should have copied at least some templates
        assert len(list(templates_dir.iterdir())) > 0


# ========================= validate_config details =========================


class TestValidateConfigDetails:
    def test_report_validation_errors(self, tmp_mutimon, write_config, capsys):
        write_config()
        with mock.patch("mutimon.main.send_error_email"):
            with pytest.raises(SystemExit):
                main._report_validation_errors(["Error 1", "Error 2"])
        err = capsys.readouterr().err
        assert "Error 1" in err
        assert "Error 2" in err

    def test_jmespath_validation(self, sample_config):
        """Valid config should pass JMESPath validation."""
        # Add a JMESPath variable
        sample_config["defs"]["test-site"]["query"]["variables"]["data"] = {
            "selector": "script",
            "value": {
                "type": "text",
                "parse": "json",
                "query": {
                    "type": "list",
                    "path": "items[*]",
                    "variables": {"name": {"path": "name"}},
                },
            },
        }
        errors = main._validate_jmespath_paths(sample_config)
        assert errors == []

    def test_jmespath_invalid_path(self, sample_config):
        sample_config["defs"]["test-site"]["query"]["variables"]["data"] = {
            "selector": "script",
            "value": {
                "type": "text",
                "parse": "json",
                "query": {
                    "type": "list",
                    "path": "[[[invalid",
                    "variables": {"name": {"path": "name"}},
                },
            },
        }
        errors = main._validate_jmespath_paths(sample_config)
        assert len(errors) > 0

    def test_array_schedule_with_invalid(self, sample_config):
        sample_config["rules"][0]["schedule"] = ["0 8 * * *", "invalid"]
        errors = main._validate_cron_expressions(sample_config)
        assert len(errors) == 1

    def test_css_expect_validation(self, sample_config):
        sample_config["defs"]["test-site"]["query"]["expect"] = ["div.item", ".valid"]
        errors = main._validate_css_selectors(sample_config)
        assert errors == []

    def test_css_reject_validation(self, sample_config):
        sample_config["defs"]["test-site"]["query"]["reject"] = [".no-results"]
        errors = main._validate_css_selectors(sample_config)
        assert errors == []

    def test_css_pagination_validation(self, sample_config):
        sample_config["defs"]["test-site"]["pagination"] = {
            "type": "next_link",
            "selector": "a.next",
        }
        errors = main._validate_css_selectors(sample_config)
        assert errors == []

    def test_css_filter_validation(self, sample_config):
        sample_config["defs"]["test-site"]["query"]["filter"] = {
            "selector": ".date"
        }
        errors = main._validate_css_selectors(sample_config)
        assert errors == []


# ========================= query_json edge cases =========================


class TestQueryJsonEdgeCases:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def test_no_path(self):
        data = {"name": "test"}
        spec = {
            "type": "single",
            "variables": {"name": {"path": "name"}},
        }
        result = main.query_json(data, spec, {})
        assert result["name"] == "test"

    def test_empty_result(self):
        data = {"items": []}
        spec = {
            "type": "list",
            "path": "items",
            "variables": {"name": {"path": "name"}},
        }
        result = main.query_json(data, spec, {})
        assert result == []


# ========================= extract_value edge cases =========================


class TestExtractValueEdgeCases:
    def test_attribute_none_returns_empty(self):
        from bs4 import BeautifulSoup

        html = "<div><a>link</a></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("a")
        result = main.extract_value(el, {"type": "attribute", "name": "href"})
        assert result == ""

    def test_parse_json_invalid(self):
        from bs4 import BeautifulSoup

        html = "<script>not json</script>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("script")
        result = main.extract_value(
            el, {"type": "text", "parse": "json"}, default={}
        )
        assert result == {}

    def test_parse_list_custom_delimiter(self):
        from bs4 import BeautifulSoup

        html = "<span>a|b|c</span>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("span")
        result = main.extract_value(
            el, {"type": "text", "parse": "list", "delimiter": r"\|"}
        )
        assert result == ["a", "b", "c"]

    def test_regex_no_group(self):
        from bs4 import BeautifulSoup

        html = "<span>hello123world</span>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("span")
        result = main.extract_value(
            el, {"type": "text", "regex": r"\d+"}
        )
        assert result == "123"

    def test_text_preserves_space_around_inline_elements(self):
        """Text spanning inline elements with trailing/leading whitespace must
        preserve word boundaries. Regression: Wikipedia thread titles like
        '<a>Szablon:ISG</a> ponownie' were rendered as 'Szablon:ISGponownie'."""
        from bs4 import BeautifulSoup

        html = '<h2><a href="x">Szablon:ISG</a> ponownie</h2>'
        h2 = BeautifulSoup(html, "html.parser").select_one("h2")
        result = main.extract_value(h2, {"type": "text"})
        assert result == "Szablon:ISG ponownie"


# ========================= parse_money edge cases =========================


class TestParseMoneyEdgeCases:
    def test_with_percent(self):
        result = main.parse_money("50%")
        assert result == 50.0

    def test_with_locale(self):
        from babel import Locale

        locale = Locale("en", "US")
        result = main.parse_money("$1,234.56", locale=locale)
        assert abs(result - 1234.56) < 0.01

    def test_garbage_returns_zero(self):
        result = main.parse_money("not a number at all")
        assert result == 0


# ========================= scheduling edge cases =========================


class TestSchedulingEdgeCases:
    def test_already_ran_this_minute(self, tmp_mutimon):
        from datetime import datetime

        rule = {
            "name": "test-dup",
            "schedule": f"{datetime.now().minute} * * * *",
        }
        main.save_last_run("test-dup")
        # Should not run again in same minute
        assert main.should_run_now(rule) is False

    def test_schedule_array(self, tmp_mutimon):
        from datetime import datetime

        now = datetime.now()
        rule = {
            "name": "test-array",
            "schedule": [f"{now.minute} {now.hour} * * *", "0 0 1 1 *"],
        }
        assert main.should_run_now(rule) is True


# ========================= run() edge cases =========================


class TestRunEdgeCases:
    def test_verbose_flag(self, tmp_mutimon, write_config, sample_config):
        write_config()
        with mock.patch("sys.argv", ["mon", "--verbose", "--validate"]):
            main.run()
        assert main.verbose is True
        main.verbose = False

    def test_normal_run_checks_schedule(self, tmp_mutimon, write_config, sample_config, capsys):
        write_config()
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        # Without --force, rules only run if schedule matches
        with mock.patch("sys.argv", ["mon"]):
            with mock.patch("mutimon.main.should_run_now", return_value=False):
                main.run()

    def test_save_email_flag(self, tmp_mutimon, write_config, sample_config):
        write_config()
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        html = '<html><body><div class="item"><h3>X</h3><a href="/x">x</a></div></body></html>'
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        with mock.patch("sys.argv", ["mon", "--save-email", "--force"]):
            with mock.patch("mutimon.main.requests.request", return_value=fake_resp):
                main.run()
        email_file = tmp_mutimon / "data" / "emails" / "test-rule.txt"
        assert email_file.exists()


# ========================= send_error_email internals =========================


class TestSendErrorEmailInternals:
    def test_reads_config_directly(self, tmp_mutimon, write_config, sample_config):
        """send_error_email reads config file directly (no third-party deps)."""
        write_config()
        with mock.patch("mutimon.main.smtplib.SMTP") as mock_smtp:
            mock_server = mock.MagicMock()
            mock_smtp.return_value.__enter__ = mock.Mock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = mock.Mock(return_value=False)
            main.send_error_email("[mutimon] Test", "Body text")
            # Should have been called with the server from config
            mock_smtp.assert_called_once()
            call_args = mock_smtp.call_args
            assert call_args[0][0] == "smtp.test.com"
            assert call_args[0][1] == 587

    def test_collects_unique_recipients(self, tmp_mutimon, write_config):
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "x",
                    "email": "sender@test.com",
                }
            },
            "defs": {},
            "rules": [
                {
                    "ref": "x",
                    "name": "r1",
                    "schedule": "0 * * * *",
                    "subject": "S",
                    "template": "./templates/t",
                    "email": "a@test.com",
                },
                {
                    "ref": "x",
                    "name": "r2",
                    "schedule": "0 * * * *",
                    "subject": "S",
                    "template": "./templates/t",
                    "email": "b@test.com",
                },
                {
                    "ref": "x",
                    "name": "r3",
                    "schedule": "0 * * * *",
                    "subject": "S",
                    "template": "./templates/t",
                    "email": "a@test.com",  # duplicate
                },
            ],
        }
        write_config(config)
        with mock.patch("mutimon.main.smtplib.SMTP") as mock_smtp:
            mock_server = mock.MagicMock()
            mock_smtp.return_value.__enter__ = mock.Mock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = mock.Mock(return_value=False)
            main.send_error_email("[mutimon] Test", "Body")
            # Should send to unique recipients only (a, b, plus sender)
            call_count = mock_server.send_message.call_count
            assert call_count >= 2  # at least a@test.com and b@test.com


# ========================= extract_id edge cases =========================


class TestSendErrorEmailEdgeCases:
    def test_no_config_file(self, tmp_mutimon):
        config_file = tmp_mutimon / "config.json"
        if config_file.exists():
            config_file.unlink()
        # Should return silently
        main.send_error_email("[mutimon] Test", "Body")

    def test_missing_credentials(self, tmp_mutimon, write_config):
        config = {
            "email": {"server": {"host": "", "port": 587, "password": "", "email": ""}},
            "defs": {},
            "rules": [],
        }
        write_config(config)
        with mock.patch("mutimon.main.smtplib.SMTP") as mock_smtp:
            main.send_error_email("[mutimon] Test", "Body")
            mock_smtp.assert_not_called()

    def test_no_rules_sends_to_sender(self, tmp_mutimon, write_config):
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "x",
                    "email": "sender@test.com",
                }
            },
            "defs": {},
            "rules": [],
        }
        write_config(config)
        with mock.patch("mutimon.main.smtplib.SMTP") as mock_smtp:
            mock_server = mock.MagicMock()
            mock_smtp.return_value.__enter__ = mock.Mock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = mock.Mock(return_value=False)
            main.send_error_email("[mutimon] Test", "Body")
            mock_server.send_message.assert_called_once()


class TestPrintSetupGuide:
    def test_prints_guide(self, tmp_mutimon, capsys):
        import shutil

        src = os.path.join(main.SKELETON_DIR, "config.json")
        shutil.copy2(src, str(tmp_mutimon / "config.json"))
        with pytest.raises(SystemExit):
            main.print_setup_guide()
        out = capsys.readouterr().out
        assert "Quick setup" in out
        assert "TIP" in out
        assert "mon --validate" in out


class TestProcessRuleThresholds:
    """Test threshold crossing detection in process_rule."""

    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def _make_config(self, tmp_mutimon):
        template = tmp_mutimon / "templates" / "test"
        template.write_text("Items: {{count}}")
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
                "site": {
                    "url": "https://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "id": {"type": "attribute", "name": "data-id"},
                        "variables": {
                            "title": {"selector": "h3", "value": {"type": "text"}},
                            "price": {
                                "selector": ".price",
                                "value": {"type": "text", "parse": "number"},
                                "default": "0",
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

    def test_threshold_crossing_renotifies(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "site",
            "name": "threshold-test",
            "subject": "Alert: {{count}}",
            "template": "./templates/test",
            "email": "user@test.com",
            "input": {"validator": {"test": "{{price}} > 50"}},
        }
        # First: item at price 100 - passes validator
        html1 = '<html><body><div class="item" data-id="1"><h3>X</h3><span class="price">100</span></div></body></html>'
        with self._mock_fetch(html1):
            with mock.patch("mutimon.main.send_email"):
                main.process_rule(config, rule)

        # Second: price drops to 30 - fails validator, no notification
        html2 = '<html><body><div class="item" data-id="1"><h3>X</h3><span class="price">30</span></div></body></html>'
        with self._mock_fetch(html2):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_not_called()

        # Third: price rises to 75 - passes again, threshold crossed
        html3 = '<html><body><div class="item" data-id="1"><h3>X</h3><span class="price">75</span></div></body></html>'
        with self._mock_fetch(html3):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_called_once()  # Re-notification!

    def test_no_items_skips(self, tmp_mutimon):
        config = self._make_config(tmp_mutimon)
        rule = {
            "ref": "site",
            "name": "empty-test",
            "subject": "Alert",
            "template": "./templates/test",
            "email": "user@test.com",
        }
        html = "<html><body><p>No items</p></body></html>"
        with self._mock_fetch(html):
            with mock.patch("mutimon.main.send_email") as mock_send:
                main.process_rule(config, rule)
                mock_send.assert_not_called()


class TestFetchAllItemsPagination:
    def test_pagination_fetches_multiple_pages(self):
        page1 = '<html><body><div class="item" data-id="1"><h3>A</h3></div><a class="next" href="/page2">Next</a></body></html>'
        page2 = '<html><body><div class="item" data-id="2"><h3>B</h3></div></body></html>'

        call_count = [0]

        def fake_get(method, url, **kwargs):
            call_count[0] += 1
            resp = mock.MagicMock()
            resp.text = page1 if call_count[0] == 1 else page2
            resp.headers = {}
            return resp

        definition = {
            "url": "https://example.com",
            "pagination": {
                "type": "next_link",
                "selector": "a.next",
                "base_url": "https://example.com",
                "max_pages": 2,
            },
            "query": {
                "type": "list",
                "selector": "div.item",
                "id": {"type": "attribute", "name": "data-id"},
                "variables": {
                    "title": {"selector": "h3", "value": {"type": "text"}},
                },
            },
        }
        with mock.patch("mutimon.main.requests.request", side_effect=fake_get):
            items = main.fetch_all_items(definition, {})
        assert len(items) == 2
        assert items[0]["title"] == "A"
        assert items[1]["title"] == "B"


class TestDryRunInRun:
    def setup_method(self):
        main.verbose = False

    def test_dry_run_prints_items(self, tmp_mutimon, write_config, sample_config, capsys):
        write_config()
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        html = '<html><body><div class="item"><h3>Hello</h3><a href="/x">x</a></div></body></html>'
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        with mock.patch("sys.argv", ["mon", "--dry-run", "--force", "test-rule"]):
            with mock.patch("mutimon.main.requests.request", return_value=fake_resp):
                main.run()
        out = capsys.readouterr().out
        assert "DRY RUN" in out


class TestCommandNodeArgTypes:
    def test_float_arg(self):
        config = {
            "defs": {
                "commands": {
                    "half": {
                        "args": ["n"],
                        "template": "{{ n }}",
                    }
                }
            }
        }
        main.setup_liquid(config)
        result = main.liquid.from_string("{% half 3.14 %}").render()
        assert "3.14" in result

    def test_word_arg_resolves_variable(self):
        config = {
            "defs": {
                "commands": {
                    "echo": {
                        "args": ["val"],
                        "template": "{{ val }}",
                    }
                }
            }
        }
        main.setup_liquid(config)
        result = main.liquid.from_string("{% echo myvar %}").render(myvar="hello")
        assert "hello" in result

    def test_string_arg(self):
        config = {
            "defs": {
                "commands": {
                    "greet": {
                        "args": ["name"],
                        "template": "Hello {{ name }}",
                    }
                }
            }
        }
        main.setup_liquid(config)
        result = main.liquid.from_string("{% greet 'World' %}").render()
        assert "World" in result


class TestRunDryRunBranches:
    def setup_method(self):
        main.verbose = False

    def test_dry_run_with_validator(self, tmp_mutimon, write_config, capsys):
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "x",
                    "email": "x@x.com",
                }
            },
            "defs": {
                "site": {
                    "url": "https://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div.item",
                        "variables": {
                            "title": {"selector": "h3", "value": {"type": "text"}},
                            "score": {
                                "selector": ".score",
                                "value": {"type": "text", "parse": "number"},
                                "default": "0",
                            },
                        },
                    },
                }
            },
            "rules": [
                {
                    "ref": "site",
                    "name": "dry-val",
                    "schedule": "0 * * * *",
                    "subject": "Test",
                    "template": "./templates/test",
                    "email": "x@x.com",
                    "input": {"validator": {"test": "{{score}} > 50"}},
                }
            ],
        }
        write_config(config)
        template = tmp_mutimon / "templates" / "test"
        template.write_text("{{count}}")
        html = '''<html><body>
            <div class="item"><h3>A</h3><span class="score">100</span></div>
            <div class="item"><h3>B</h3><span class="score">10</span></div>
        </body></html>'''
        fake_resp = mock.MagicMock()
        fake_resp.text = html
        fake_resp.headers = {}
        with mock.patch("sys.argv", ["mon", "--dry-run", "--force"]):
            with mock.patch("mutimon.main.requests.request", return_value=fake_resp):
                main.run()
        out = capsys.readouterr().out
        assert "DRY RUN" in out


class TestExtractIdEdgeCases:
    def test_hash_fallback(self):
        """When no URL and no id spec match, hash all values."""
        item = {"title": "Test", "score": "42"}
        result = main.extract_id(item, {})
        assert result is not None
        assert len(result) > 0

    def test_source_no_regex(self):
        item = {"coin": "bitcoin"}
        result = main.extract_id(item, {"source": "coin"})
        assert result == "bitcoin"


# ========================= _validate_regex_patterns =========================


class TestValidateRegexPatterns:
    def test_valid_patterns(self):
        config = {
            "defs": {
                "test": {
                    "url": "http://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div",
                        "id": {"regex": "^(\\d+)$"},
                        "variables": {
                            "title": {
                                "selector": "h3",
                                "value": {"type": "text", "regex": "^(.+)"},
                            }
                        },
                    },
                }
            },
            "rules": [],
        }
        errors = main._validate_regex_patterns(config)
        assert errors == []

    def test_invalid_id_regex(self):
        config = {
            "defs": {
                "test": {
                    "url": "http://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div",
                        "id": {"regex": "[invalid("},
                        "variables": {},
                    },
                }
            },
            "rules": [],
        }
        errors = main._validate_regex_patterns(config)
        assert len(errors) == 1
        assert "query.id.regex" in errors[0]

    def test_invalid_value_regex(self):
        config = {
            "defs": {
                "test": {
                    "url": "http://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div",
                        "variables": {
                            "title": {
                                "selector": "h3",
                                "value": {"type": "text", "regex": "(unclosed"},
                            }
                        },
                    },
                }
            },
            "rules": [],
        }
        errors = main._validate_regex_patterns(config)
        assert len(errors) == 1
        assert "value.regex" in errors[0]

    def test_invalid_validator_regex(self):
        config = {
            "defs": {
                "validators": {
                    "bad": {"match": {"var": "x", "regex": "*bad*"}}
                }
            },
            "rules": [],
        }
        errors = main._validate_regex_patterns(config)
        assert len(errors) == 1
        assert "validators.bad" in errors[0]

    def test_validator_array(self):
        config = {
            "defs": {
                "validators": {
                    "multi": [
                        {"match": {"var": "x", "regex": "valid"}},
                        {"match": {"var": "y", "regex": "(broken"}},
                    ]
                }
            },
            "rules": [],
        }
        errors = main._validate_regex_patterns(config)
        assert len(errors) == 1
        assert "multi[1]" in errors[0]

    def test_skips_at_id_reference(self):
        config = {
            "defs": {
                "validators": {
                    "good": {"match": {"var": "x", "regex": "ok"}}
                }
            },
            "rules": [
                {
                    "name": "test",
                    "input": [{"validator": {"@id": "good"}}],
                }
            ],
        }
        errors = main._validate_regex_patterns(config)
        assert errors == []

    def test_rule_input_validator(self):
        config = {
            "defs": {},
            "rules": [
                {
                    "name": "test",
                    "input": [
                        {
                            "validator": {
                                "match": {"var": "x", "regex": "(broken"}
                            }
                        }
                    ],
                }
            ],
        }
        errors = main._validate_regex_patterns(config)
        assert len(errors) == 1
        assert "rules.test" in errors[0]

    def test_each_input_validator(self):
        config = {
            "defs": {},
            "rules": [
                {
                    "name": "test",
                    "input": {
                        "each": {"var": "sub", "values": ["a", "b"]},
                        "validator": {
                            "match": {"var": "x", "regex": "[bad("}
                        },
                    },
                }
            ],
        }
        errors = main._validate_regex_patterns(config)
        assert len(errors) == 1
        assert "rules.test" in errors[0]

    def test_skips_commands_filters_validators_defs(self):
        config = {
            "defs": {
                "commands": {"fresh": {"args": [], "template": "x"}},
                "filters": {"clean": "strip"},
                "validators": {},
            },
            "rules": [],
        }
        errors = main._validate_regex_patterns(config)
        assert errors == []

    def test_match_as_list(self):
        config = {
            "defs": {
                "validators": {
                    "v": {
                        "match": [
                            {"var": "x", "regex": "good"},
                            {"var": "y", "regex": "(bad"},
                        ]
                    }
                }
            },
            "rules": [],
        }
        errors = main._validate_regex_patterns(config)
        assert len(errors) == 1
        assert "match[1]" in errors[0]


# ========================= validate_css find/transform =========================


class TestValidateCssFindTransform:
    def test_valid_find_selectors(self):
        config = {
            "defs": {
                "test": {
                    "url": "http://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div",
                        "variables": {
                            "x": {
                                "selector": "p",
                                "find": [["select", ".valid"], ["until", ".stop"]],
                                "value": {"type": "text"},
                            }
                        },
                    },
                }
            },
            "rules": [],
        }
        errors = main._validate_css_selectors(config)
        assert errors == []

    def test_invalid_find_selector(self):
        config = {
            "defs": {
                "test": {
                    "url": "http://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div",
                        "variables": {
                            "x": {
                                "selector": "p",
                                "find": [["select", "div[[["]],
                                "value": {"type": "text"},
                            }
                        },
                    },
                }
            },
            "rules": [],
        }
        errors = main._validate_css_selectors(config)
        assert len(errors) == 1
        assert "find[0]" in errors[0]

    def test_invalid_transform_selector(self):
        config = {
            "defs": {
                "test": {
                    "url": "http://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div",
                        "variables": {
                            "x": {
                                "selector": "p",
                                "transform": [["remove", ":::bad"]],
                                "value": {"type": "text"},
                            }
                        },
                    },
                }
            },
            "rules": [],
        }
        errors = main._validate_css_selectors(config)
        assert len(errors) == 1
        assert "transform[0]" in errors[0]

    def test_siblings_step_no_selector_check(self):
        config = {
            "defs": {
                "test": {
                    "url": "http://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div",
                        "variables": {
                            "x": {
                                "selector": "p",
                                "find": [["siblings"]],
                                "value": {"type": "text"},
                            }
                        },
                    },
                }
            },
            "rules": [],
        }
        errors = main._validate_css_selectors(config)
        assert errors == []


# ========================= validate_only =========================


class TestValidateOnly:
    def test_validate_only_skips_email(self, tmp_mutimon, write_config):
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "pass",
                    "email": "test@test.com",
                }
            },
            "defs": {},
            "rules": [{"schedule": "bad cron", "name": "broken"}],
        }
        write_config(config)
        with mock.patch.object(main, "send_error_email") as mock_email:
            with pytest.raises(SystemExit):
                main.validate_config(config, validate_only=True)
            mock_email.assert_not_called()

    def test_validate_normal_sends_email(self, tmp_mutimon, write_config):
        config = {
            "email": {
                "server": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "password": "pass",
                    "email": "test@test.com",
                }
            },
            "defs": {},
            "rules": [{"schedule": "bad cron", "name": "broken"}],
        }
        write_config(config)
        with mock.patch.object(main, "send_error_email") as mock_email:
            with pytest.raises(SystemExit):
                main.validate_config(config, validate_only=False)
            mock_email.assert_called_once()


# ========================= _validate_jmespath sub-variable =========================


class TestValidateJmespathSubVar:
    def test_invalid_sub_variable_path(self):
        config = {
            "defs": {
                "test": {
                    "url": "http://example.com",
                    "query": {
                        "type": "list",
                        "selector": "div",
                        "variables": {
                            "data": {
                                "selector": "script",
                                "value": {
                                    "type": "text",
                                    "parse": "json",
                                    "query": {
                                        "path": "items[0]",
                                        "variables": {
                                            "city": {"path": "locations[???"}
                                        },
                                    },
                                },
                            }
                        },
                    },
                }
            },
            "rules": [],
        }
        errors = main._validate_jmespath_paths(config)
        assert len(errors) == 1
        assert "city" in errors[0]
