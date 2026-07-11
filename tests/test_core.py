"""Unit tests for mutimon core functions."""

import json
import os
from datetime import datetime
from unittest import mock

import pytest
from bs4 import BeautifulSoup

from mutimon import main


# ========================= Logging =========================


class TestLogging:
    def teardown_method(self):
        main.verbose = False

    def test_log_silent_by_default(self, capsys):
        main.verbose = False
        main.log("hidden")
        assert capsys.readouterr().out == ""

    def test_log_verbose(self, capsys):
        main.verbose = True
        main.log("visible")
        assert "visible" in capsys.readouterr().out

    def test_info_always_prints(self, capsys):
        main.verbose = False
        main.info("always")
        assert "always" in capsys.readouterr().out


# ========================= Config =========================


class TestInitConfig:
    def test_creates_skeleton(self, tmp_mutimon, monkeypatch):
        config_file = tmp_mutimon / "config.json"
        # init_config exits after creating, so catch SystemExit
        with pytest.raises(SystemExit):
            main.init_config()
        assert config_file.exists()

    def test_skips_if_config_exists(self, tmp_mutimon):
        config_file = tmp_mutimon / "config.json"
        config_file.write_text("{}")
        # Should return without doing anything
        main.init_config()

    def test_creates_directories(self, tmp_mutimon):
        data_dir = tmp_mutimon / "data"
        templates_dir = tmp_mutimon / "templates"
        # Remove dirs to test creation
        data_dir.rmdir()
        templates_dir.rmdir()
        with pytest.raises(SystemExit):
            main.init_config()
        assert data_dir.exists()
        assert templates_dir.exists()


class TestIsSkeletonConfig:
    def test_true_when_unchanged(self, tmp_mutimon):
        import shutil

        src = os.path.join(main.SKELETON_DIR, "config.json")
        dst = tmp_mutimon / "config.json"
        shutil.copy2(src, str(dst))
        assert main.is_skeleton_config() is True

    def test_false_when_modified(self, tmp_mutimon):
        config_file = tmp_mutimon / "config.json"
        config_file.write_text('{"custom": true}')
        assert main.is_skeleton_config() is False

    def test_false_when_missing(self, tmp_mutimon):
        assert main.is_skeleton_config() is False


class TestHashDict:
    def test_same_dict_same_hash(self):
        d = {"a": 1, "b": 2}
        assert main._hash_dict(d) == main._hash_dict(d)

    def test_order_independent(self):
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 2, "a": 1}
        assert main._hash_dict(d1) == main._hash_dict(d2)

    def test_different_dict_different_hash(self):
        d1 = {"a": 1}
        d2 = {"a": 2}
        assert main._hash_dict(d1) != main._hash_dict(d2)


class TestLoadSkeletonEmailServer:
    def test_loads_server(self):
        server = main._load_skeleton_email_server()
        assert server is not None
        assert server["host"] == "smtp.example.com"
        assert server["email"] == "you@example.com"

    def test_returns_none_if_missing(self, monkeypatch):
        monkeypatch.setattr(main, "SKELETON_DIR", "/nonexistent")
        assert main._load_skeleton_email_server() is None


class TestLoadConfig:
    def test_loads_valid_json(self, write_config, sample_config):
        write_config()
        config = main.load_config()
        assert config["email"]["server"]["host"] == "smtp.test.com"

    def test_raises_on_invalid_json(self, tmp_mutimon):
        config_file = tmp_mutimon / "config.json"
        config_file.write_text("not json")
        with pytest.raises(json.JSONDecodeError):
            main.load_config()


# ========================= Validation =========================


class TestValidateConfig:
    def test_valid_config_passes(self, write_config, sample_config):
        write_config()
        # Should not raise
        main.validate_config(sample_config)

    def test_invalid_schema_exits(self, write_config):
        bad_config = {"missing": "required fields"}
        with pytest.raises(SystemExit):
            main.validate_config(bad_config)

    def test_invalid_cron_exits(self, write_config, sample_config):
        sample_config["rules"][0]["schedule"] = "not a cron"
        with pytest.raises(SystemExit):
            main.validate_config(sample_config)

    def test_invalid_css_selector_exits(self, write_config, sample_config):
        sample_config["defs"]["test-site"]["query"]["selector"] = "[[[invalid"
        with pytest.raises(SystemExit):
            main.validate_config(sample_config)


class TestValidateCronExpressions:
    def test_valid_cron(self, sample_config):
        errors = main._validate_cron_expressions(sample_config)
        assert errors == []

    def test_invalid_cron(self, sample_config):
        sample_config["rules"][0]["schedule"] = "bad"
        errors = main._validate_cron_expressions(sample_config)
        assert len(errors) == 1

    def test_array_schedule(self, sample_config):
        sample_config["rules"][0]["schedule"] = ["0 8 * * *", "0 20 * * *"]
        errors = main._validate_cron_expressions(sample_config)
        assert errors == []


class TestValidateCssSelectors:
    def test_valid_selectors(self, sample_config):
        errors = main._validate_css_selectors(sample_config)
        assert errors == []

    def test_self_selector_skipped(self, sample_config):
        sample_config["defs"]["test-site"]["query"]["variables"]["self_ref"] = {
            "selector": ":self",
            "value": {"type": "text"},
        }
        errors = main._validate_css_selectors(sample_config)
        assert errors == []


# ========================= State =========================


class TestState:
    def test_save_and_load(self, tmp_mutimon):
        items = [{"id": "1", "title": "test"}]
        main.save_state("test-rule", items)
        loaded = main.load_state("test-rule")
        assert loaded == items

    def test_load_missing_returns_empty(self, tmp_mutimon):
        assert main.load_state("nonexistent") == []

    def test_load_corrupt_returns_empty(self, tmp_mutimon):
        state_file = tmp_mutimon / "data" / "corrupt-rule"
        state_file.write_text("not json")
        assert main.load_state("corrupt-rule") == []

    def test_save_and_load_last_run(self, tmp_mutimon):
        main.save_last_run("test-rule")
        last = main.load_last_run("test-rule")
        assert last is not None
        # Should be within last few seconds
        delta = datetime.now() - last
        assert delta.total_seconds() < 5

    def test_load_last_run_missing(self, tmp_mutimon):
        assert main.load_last_run("nonexistent") is None


# ========================= Scheduling =========================


class TestShouldRunNow:
    def test_no_schedule_always_runs(self):
        rule = {"name": "test"}
        assert main.should_run_now(rule) is True

    def test_matching_schedule(self, tmp_mutimon):
        now = datetime.now()
        rule = {
            "name": "test",
            "schedule": f"{now.minute} {now.hour} * * *",
        }
        assert main.should_run_now(rule) is True

    def test_non_matching_schedule(self, tmp_mutimon):
        rule = {
            "name": "test",
            "schedule": "0 0 1 1 *",  # Midnight Jan 1st only
        }
        assert main.should_run_now(rule) is False


# ========================= Extraction =========================


class TestExtractValue:
    def setup_method(self):
        self.soup = BeautifulSoup(
            '<div><h3>Hello World</h3><a href="/test" class="link">Click</a>'
            '<span class="num">42 points</span></div>',
            "html.parser",
        )

    def test_text_extraction(self):
        el = self.soup.select_one("h3")
        result = main.extract_value(el, {"type": "text"})
        assert result == "Hello World"

    def test_attribute_extraction(self):
        el = self.soup.select_one("a")
        result = main.extract_value(el, {"type": "attribute", "name": "href"})
        assert result == "/test"

    def test_regex_extraction(self):
        el = self.soup.select_one("span.num")
        result = main.extract_value(
            el, {"type": "text", "regex": r"(\d+)"}
        )
        assert result == "42"

    def test_prefix(self):
        el = self.soup.select_one("a")
        result = main.extract_value(
            el, {"type": "attribute", "name": "href", "prefix": "https://example.com"}
        )
        assert result == "https://example.com/test"

    def test_parse_url_protocol_relative(self):
        html = '<a href="//pl.wikipedia.org/wiki/Foo">link</a>'
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("a")
        result = main.extract_value(
            el, {"type": "attribute", "name": "href",
                 "parse": "url", "prefix": "https://pl.wikipedia.org"}
        )
        assert result == "https://pl.wikipedia.org/wiki/Foo"

    def test_parse_url_absolute_path(self):
        html = '<a href="/w/index.php?diff=123">link</a>'
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("a")
        result = main.extract_value(
            el, {"type": "attribute", "name": "href",
                 "parse": "url", "prefix": "https://pl.wikipedia.org"}
        )
        assert result == "https://pl.wikipedia.org/w/index.php?diff=123"

    def test_parse_url_full_url(self):
        html = '<a href="https://other.example.com/page">link</a>'
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("a")
        result = main.extract_value(
            el, {"type": "attribute", "name": "href",
                 "parse": "url", "prefix": "https://pl.wikipedia.org"}
        )
        assert result == "https://other.example.com/page"

    def test_parse_url_relative(self):
        html = '<a href="page/sub">link</a>'
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("a")
        result = main.extract_value(
            el, {"type": "attribute", "name": "href",
                 "parse": "url", "prefix": "https://example.com/base/"}
        )
        assert result == "https://example.com/base/page/sub"

    def test_parse_number(self):
        el = self.soup.select_one("span.num")
        result = main.extract_value(
            el, {"type": "text", "regex": r"(\d+)", "parse": "number"}
        )
        assert result == 42.0

    def test_parse_list(self):
        html = '<span>a, b, c</span>'
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("span")
        result = main.extract_value(el, {"type": "text", "parse": "list"})
        assert result == ["a", "b", "c"]

    def test_parse_json(self):
        html = '<script>{"key": "value"}</script>'
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("script")
        result = main.extract_value(el, {"type": "text", "parse": "json"})
        assert result == {"key": "value"}

    def test_parse_date_extract_value(self):
        from babel import Locale
        html = "<span>10 lip 2026</span>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("span")
        result = main.extract_value(
            el, {"type": "text", "parse": "date"}, locale=Locale.parse("pl")
        )
        assert result == "2026-07-10"

    def test_none_element_returns_default(self):
        result = main.extract_value(None, {"type": "text"}, default="fallback")
        assert result == "fallback"

    def test_empty_text_returns_default(self):
        html = "<span></span>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("span")
        result = main.extract_value(el, {"type": "text"}, default="empty")
        assert result == "empty"

    def test_html_type(self):
        html = "<div><p>Hello <b>world</b></p></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("p")
        result = main.extract_value(el, {"type": "html"})
        assert "<b>world</b>" in result

    def test_html_empty_returns_default(self):
        html = "<div><p></p></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("p")
        result = main.extract_value(el, {"type": "html"}, default="fallback")
        assert result == "fallback"

    def test_money_parse(self):
        html = "<span>1 234,50 zł</span>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("span")
        result = main.extract_value(
            el, {"type": "text", "parse": "money"}, locale="pl_PL"
        )
        assert isinstance(result, (int, float))

    def test_unknown_type_returns_default(self):
        el = self.soup.select_one("h3")
        result = main.extract_value(el, {"type": "unknown"}, default="x")
        assert result == "x"

    def test_regex_no_match_returns_default(self):
        el = self.soup.select_one("h3")
        result = main.extract_value(
            el, {"type": "text", "regex": r"(\d+)"}, default="none"
        )
        assert result == "none"


class TestParseNumber:
    def test_integer(self):
        assert main.parse_number("42") == 42.0

    def test_float(self):
        assert main.parse_number("3.14") == 3.14

    def test_with_thousands_separator(self):
        assert main.parse_number("1,234") == 1234.0

    def test_invalid_returns_zero(self):
        assert main.parse_number("abc") == 0

    def test_empty_returns_zero(self):
        assert main.parse_number("") == 0


class TestParseMoney:
    def test_simple_price(self):
        result = main.parse_money("$100.50")
        assert result == 100.50

    def test_with_currency_symbol(self):
        result = main.parse_money("$1,234.56")
        assert abs(result - 1234.56) < 0.01

    def test_empty_returns_zero(self):
        assert main.parse_money("") == 0


class TestParseDate:
    def _pl(self):
        from babel import Locale
        return Locale.parse("pl")

    def test_iso_passthrough(self):
        assert main.parse_date("2026-07-10") == "2026-07-10"

    def test_polish_abbreviated_with_year(self):
        assert main.parse_date("10 lip 2026", locale=self._pl()) == "2026-07-10"

    def test_polish_genitive_heading_uses_current_year(self):
        # Section headings omit the year (e.g. "10 lipca"); default to now's year.
        year = datetime.now().year
        assert main.parse_date("10 lipca", locale=self._pl()) == f"{year}-07-10"

    def test_polish_signature_timestamp(self):
        # Full signature: "23:52, 10 lip 2026 (CEST)".
        result = main.parse_date("23:52, 10 lip 2026 (CEST)", locale=self._pl())
        assert result == "2026-07-10"

    def test_polish_month_with_diacritics(self):
        # października -> October (10); paź -> October too.
        assert main.parse_date("5 października 2026", locale=self._pl()) == "2026-10-05"

    def test_english_fallback(self):
        from babel import Locale
        result = main.parse_date("10 July 2026", locale=Locale.parse("en"))
        assert result == "2026-07-10"

    def test_unparseable_returns_original(self):
        assert main.parse_date("not a date", locale=self._pl()) == "not a date"

    def test_empty_string(self):
        assert main.parse_date("", locale=self._pl()) == ""

    def test_no_locale_defaults_english(self):
        assert main.parse_date("2026-07-10") == "2026-07-10"


class TestExtractVariables:
    def test_basic_extraction(self, sample_html):
        soup = BeautifulSoup(sample_html, "html.parser")
        element = soup.select_one("div.item")
        variables = {
            "title": {"selector": "h3", "value": {"type": "text"}},
            "url": {
                "selector": "a",
                "value": {"type": "attribute", "name": "href"},
            },
        }
        result = main.extract_variables(element, variables)
        assert result["title"] == "First Item"
        assert result["url"] == "/page/1"

    def test_collect_mode(self, sample_html):
        soup = BeautifulSoup(sample_html, "html.parser")
        element = soup.select("div.item")[0]
        variables = {
            "tags": {
                "selector": ".tags span",
                "value": {"type": "text"},
                "collect": True,
            },
        }
        result = main.extract_variables(element, variables)
        assert result["tags"] == ["python", "web"]

    def test_default_value(self, sample_html):
        soup = BeautifulSoup(sample_html, "html.parser")
        element = soup.select("div.item")[2]  # Third item, no tags
        variables = {
            "tags": {
                "selector": ".tags span",
                "value": {"type": "text"},
                "collect": True,
            },
        }
        result = main.extract_variables(element, variables)
        assert result["tags"] == []

    def test_self_selector(self):
        html = '<a href="/link" class="item"><span>Title</span></a>'
        soup = BeautifulSoup(html, "html.parser")
        element = soup.select_one("a.item")
        variables = {
            "url": {
                "selector": ":self",
                "value": {"type": "attribute", "name": "href"},
            },
        }
        result = main.extract_variables(element, variables)
        assert result["url"] == "/link"


class TestExtractId:
    def test_attribute_id(self):
        html = '<div data-id="abc"><h3>Test</h3></div>'
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("div")
        result = main.extract_id(
            {}, {"type": "attribute", "name": "data-id"}, element=el
        )
        assert result == "abc"

    def test_source_regex_id(self):
        item = {"url": "https://example.com/page/123"}
        result = main.extract_id(item, {"source": "url", "regex": r"/page/(\d+)"})
        assert result == "123"

    def test_fallback_to_url(self):
        item = {"url": "https://example.com/unique"}
        result = main.extract_id(item, {})
        assert result == "https://example.com/unique"


class TestParseItems:
    def test_parse_list(self, sample_html):
        query = {
            "type": "list",
            "selector": "div.item",
            "id": {"type": "attribute", "name": "data-id"},
            "variables": {
                "title": {"selector": "h3", "value": {"type": "text"}},
            },
        }
        items = main.parse_items(sample_html, query)
        assert len(items) == 3
        assert items[0]["title"] == "First Item"
        assert items[0]["id"] == "1"
        assert items[2]["title"] == "Third Item"

    def test_parse_single(self):
        html = '<div class="main"><h1>Page Title</h1><span>123</span></div>'
        query = {
            "type": "single",
            "selector": "div.main",
            "variables": {
                "title": {"selector": "h1", "value": {"type": "text"}},
                "value": {"selector": "span", "value": {"type": "text"}},
            },
        }
        items = main.parse_items(html, query)
        assert len(items) == 1
        assert items[0]["title"] == "Page Title"

    def test_parse_single_no_match(self):
        html = "<div>Nothing</div>"
        query = {
            "type": "single",
            "selector": "div.item",
            "variables": {"title": {"selector": "h3", "value": {"type": "text"}}},
        }
        items = main.parse_items(html, query)
        assert items == []

    def test_parse_unknown_type(self):
        html = '<div class="item"><h3>Title</h3></div>'
        query = {
            "type": "unknown",
            "selector": "div.item",
            "variables": {},
        }
        items = main.parse_items(html, query)
        assert items == []

    def test_filter_excludes(self, sample_html):
        query = {
            "type": "list",
            "selector": "div.item",
            "filter": {"selector": ".tags", "exclude_class": None},
            "variables": {
                "title": {"selector": "h3", "value": {"type": "text"}},
            },
        }
        # Only items with .tags should be included (first two)
        items = main.parse_items(sample_html, query)
        assert len(items) == 2


# ========================= Pagination =========================


class TestFindNextPageUrl:
    def test_next_link(self):
        html = '<a class="more" href="/page2">More</a>'
        spec = {
            "type": "next_link",
            "selector": "a.more",
            "base_url": "https://example.com",
        }
        result = main.find_next_page_url(html, spec, "https://example.com/page1")
        assert result == "https://example.com/page2"

    def test_next_link_not_found(self):
        html = "<p>No links</p>"
        spec = {"type": "next_link", "selector": "a.more"}
        result = main.find_next_page_url(html, spec, "https://example.com")
        assert result is None

    def test_numbered_pagination(self):
        html = '<a class="page active" href="/p/1">1</a><a class="page" href="/p/2">2</a>'
        spec = {
            "type": "numbered",
            "selector": "a.page",
            "active_class": "active",
            "base_url": "https://example.com",
        }
        result = main.find_next_page_url(
            html, spec, "https://example.com/p/1"
        )
        assert result == "https://example.com/p/2"


class TestCheckExpect:
    def test_all_present(self, sample_html):
        missing = main.check_expect(sample_html, ["div.item", "h3"], "http://test")
        assert missing == []

    def test_missing_selector(self, sample_html):
        missing = main.check_expect(
            sample_html, ["div.item", ".nonexistent"], "http://test"
        )
        assert ".nonexistent" in missing

    def test_empty_expect(self, sample_html):
        assert main.check_expect(sample_html, [], "http://test") == []

    def test_none_expect(self, sample_html):
        assert main.check_expect(sample_html, None, "http://test") == []


# ========================= Validators =========================


class TestEvaluateSingleValidator:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def test_numexpr_pass(self):
        item = {"price": 100.0}
        validator = {"test": "{{price}} > 50"}
        assert main.evaluate_single_validator(validator, item) is True

    def test_numexpr_fail(self):
        item = {"price": 10.0}
        validator = {"test": "{{price}} > 50"}
        assert main.evaluate_single_validator(validator, item) is False

    def test_regex_match(self):
        item = {"title": "Ask HN: Something"}
        validator = {"match": {"var": "title", "regex": "^Ask HN"}}
        assert main.evaluate_single_validator(validator, item) is True

    def test_regex_no_match(self):
        item = {"title": "Show HN: Something"}
        validator = {"match": {"var": "title", "regex": "^Ask HN"}}
        assert main.evaluate_single_validator(validator, item) is False

    def test_regex_exist_false(self):
        item = {"status": "Coming soon"}
        validator = {
            "match": {"var": "status", "regex": "Coming soon", "exist": False}
        }
        assert main.evaluate_single_validator(validator, item) is False

    def test_regex_exist_false_passes(self):
        item = {"status": "Available"}
        validator = {
            "match": {"var": "status", "regex": "Coming soon", "exist": False}
        }
        assert main.evaluate_single_validator(validator, item) is True

    def test_exclude_list(self):
        item = {"skills": ["Python", "JavaScript", "Java"]}
        validator = {"match": {"var": "skills", "exclude": ["Java"]}}
        assert main.evaluate_single_validator(validator, item) is False

    def test_exclude_list_passes(self):
        item = {"skills": ["Python", "JavaScript"]}
        validator = {"match": {"var": "skills", "exclude": ["Java"]}}
        assert main.evaluate_single_validator(validator, item) is True

    def test_exclude_list_javascript_not_java(self):
        """Java in exclude should not match JavaScript in a list."""
        item = {"skills": ["JavaScript", "React"]}
        validator = {"match": {"var": "skills", "exclude": ["Java"]}}
        assert main.evaluate_single_validator(validator, item) is True

    def test_include_list(self):
        item = {"skills": ["Python", "JavaScript"]}
        validator = {"match": {"var": "skills", "include": ["Python"]}}
        assert main.evaluate_single_validator(validator, item) is True

    def test_include_list_fails(self):
        item = {"skills": ["Rust", "Go"]}
        validator = {"match": {"var": "skills", "include": ["Python"]}}
        assert main.evaluate_single_validator(validator, item) is False

    def test_exclude_string_substring(self):
        item = {"title": "Angular Developer Needed"}
        validator = {"match": {"var": "title", "exclude": ["Angular"]}}
        assert main.evaluate_single_validator(validator, item) is False

    def test_exclude_string_strict(self):
        item = {"title": "Angular"}
        validator = {
            "match": {"var": "title", "exclude": ["Angular"], "strict": True}
        }
        assert main.evaluate_single_validator(validator, item) is False

    def test_exclude_string_strict_no_match(self):
        item = {"title": "Angular Developer"}
        validator = {
            "match": {"var": "title", "exclude": ["Angular"], "strict": True}
        }
        assert main.evaluate_single_validator(validator, item) is True

    def test_match_with_value_template(self):
        item = {"first": "hello", "last": "world"}
        validator = {
            "match": {"value": "{{first}} {{last}}", "regex": "hello world"}
        }
        assert main.evaluate_single_validator(validator, item) is True

    def test_match_array_and_logic(self):
        item = {"title": "Python Dev", "company": "Acme"}
        validator = {
            "match": [
                {"var": "title", "regex": "Python"},
                {"var": "company", "regex": "Acme"},
            ]
        }
        assert main.evaluate_single_validator(validator, item) is True

    def test_match_array_one_fails(self):
        item = {"title": "Python Dev", "company": "Other"}
        validator = {
            "match": [
                {"var": "title", "regex": "Python"},
                {"var": "company", "regex": "Acme"},
            ]
        }
        assert main.evaluate_single_validator(validator, item) is False


class TestEvaluateValidator:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def test_none_passes(self):
        assert main.evaluate_validator(None, {}) is True

    def test_empty_passes(self):
        assert main.evaluate_validator({}, {}) is True

    def test_single_dict(self):
        item = {"price": 100.0}
        assert main.evaluate_validator({"test": "{{price}} > 50"}, item) is True

    def test_array_or_logic(self):
        item = {"price": 75.0}
        validators = [
            {"test": "{{price}} > 100"},
            {"test": "{{price}} > 50"},
        ]
        assert main.evaluate_validator(validators, item) is True

    def test_array_all_fail(self):
        item = {"price": 10.0}
        validators = [
            {"test": "{{price}} > 100"},
            {"test": "{{price}} > 50"},
        ]
        assert main.evaluate_validator(validators, item) is False

    def test_required_validator(self):
        item = {"price": 75.0, "score": 60.0}
        validators = [
            {"require": True, "test": "{{score}} > 50"},
            {"test": "{{price}} > 100"},
            {"test": "{{price}} > 50"},
        ]
        assert main.evaluate_validator(validators, item) is True

    def test_required_fails_blocks_all(self):
        item = {"price": 75.0, "score": 10.0}
        validators = [
            {"require": True, "test": "{{score}} > 50"},
            {"test": "{{price}} > 50"},
        ]
        assert main.evaluate_validator(validators, item) is False


class TestResolveValidator:
    def test_id_reference(self):
        defs = {"my-filter": {"test": "{{price}} > 10"}}
        result = main.resolve_validator({"@id": "my-filter"}, defs)
        assert result == {"test": "{{price}} > 10"}

    def test_id_in_array(self):
        defs = {"filter1": {"test": "{{x}} > 1"}}
        result = main.resolve_validator(
            [{"@id": "filter1"}, {"test": "{{y}} > 2"}], defs
        )
        assert len(result) == 2
        assert result[0] == {"test": "{{x}} > 1"}

    def test_passthrough_without_id(self):
        validator = {"test": "{{price}} > 10"}
        result = main.resolve_validator(validator, {})
        assert result is validator

    def test_none_passthrough(self):
        assert main.resolve_validator(None, {}) is None


class TestResolveInputs:
    def test_no_input(self):
        rule = {"params": {"q": "test"}}
        result = main.resolve_inputs(rule)
        assert len(result) == 1
        assert result[0]["params"] == {"q": "test"}
        assert result[0]["validator"] is None

    def test_single_input(self):
        rule = {"input": {"params": {"q": "x"}, "validator": {"test": "1 > 0"}}}
        result = main.resolve_inputs(rule)
        assert len(result) == 1
        assert result[0]["params"] == {"q": "x"}

    def test_array_input(self):
        rule = {
            "input": [
                {"params": {"q": "a"}},
                {"params": {"q": "b"}},
            ]
        }
        result = main.resolve_inputs(rule)
        assert len(result) == 2

    def test_each_input(self):
        rule = {
            "input": {
                "each": {"var": "sub", "values": ["a", "b"]},
                "params": {"feed": "{{sub}}"},
            }
        }
        result = main.resolve_inputs(rule)
        assert len(result) == 2
        assert result[0]["params"]["feed"] == "a"
        assert result[1]["params"]["feed"] == "b"

    def test_def_validator_used_when_no_input_validator(self):
        rule = {"params": {"q": "test"}}
        definition = {"validator": {"test": "{{ score }} > 10"}}
        result = main.resolve_inputs(rule, definition=definition)
        assert result[0]["validator"] == {"test": "{{ score }} > 10"}

    def test_def_validator_merged_with_input_validator(self):
        rule = {
            "input": {
                "params": {"q": "x"},
                "validator": {"match": {"var": "title", "regex": "^Ask"}},
            }
        }
        definition = {"validator": {"test": "{{ score }} > 10"}}
        result = main.resolve_inputs(rule, definition=definition)
        v = result[0]["validator"]
        assert isinstance(v, list)
        assert len(v) == 2
        assert v[0] == {"test": "{{ score }} > 10", "require": True}
        assert v[1] == {"match": {"var": "title", "regex": "^Ask"}}

    def test_def_validator_merged_with_input_array_validator(self):
        rule = {
            "input": {
                "params": {"q": "x"},
                "validator": [
                    {"match": {"var": "title", "regex": "^Ask"}},
                    {"match": {"var": "title", "regex": "^Show"}},
                ],
            }
        }
        definition = {"validator": {"test": "{{ score }} > 10"}}
        result = main.resolve_inputs(rule, definition=definition)
        v = result[0]["validator"]
        assert isinstance(v, list)
        assert len(v) == 3
        assert v[0] == {"test": "{{ score }} > 10", "require": True}

    def test_def_array_validator_merged_with_input(self):
        definition = {
            "validator": [
                {"test": "{{ score }} > 5", "require": True},
                {"test": "{{ score }} > 10"},
            ]
        }
        rule = {
            "input": {"params": {"q": "x"}, "validator": {"test": "{{ age }} < 7"}}
        }
        result = main.resolve_inputs(rule, definition=definition)
        v = result[0]["validator"]
        assert isinstance(v, list)
        assert len(v) == 3
        assert v[0] == {"test": "{{ score }} > 5", "require": True}
        assert v[1] == {"test": "{{ score }} > 10", "require": True}
        assert v[2] == {"test": "{{ age }} < 7"}

    def test_def_track_used_when_no_input_track(self):
        rule = {"params": {"url": "https://example.com"}}
        definition = {
            "track": {
                "states": [
                    {"name": "down", "test": "{{ http.code }} == 0"},
                    {"name": "up", "test": "{{ http.code }} >= 200"},
                ]
            }
        }
        result = main.resolve_inputs(rule, definition=definition)
        assert result[0]["track"] is not None
        assert result[0]["track"]["states"][0]["name"] == "down"

    def test_input_track_overrides_def_track(self):
        definition = {
            "track": {
                "states": [
                    {"name": "down", "test": "{{ http.code }} == 0"},
                    {"name": "up", "test": "{{ http.code }} >= 200"},
                ]
            }
        }
        rule = {
            "input": {
                "params": {"url": "https://example.com"},
                "track": {
                    "states": [
                        {"name": "offline", "test": "{{ http.code }} >= 500"},
                        {"name": "online", "test": "{{ http.code }} > 0"},
                    ]
                },
            }
        }
        result = main.resolve_inputs(rule, definition=definition)
        assert result[0]["track"]["states"][0]["name"] == "offline"

    def test_def_track_ignored_when_input_has_validator(self):
        definition = {
            "track": {
                "states": [{"name": "up", "test": "{{ http.code }} >= 200"}]
            }
        }
        rule = {
            "input": {
                "params": {"q": "x"},
                "validator": {"test": "{{ score }} > 10"},
            }
        }
        result = main.resolve_inputs(rule, definition=definition)
        assert result[0]["track"] is None
        assert result[0]["validator"] is not None

    def test_def_validator_ignored_when_input_has_track(self):
        definition = {"validator": {"test": "{{ score }} > 10"}}
        rule = {
            "input": {
                "params": {"q": "x"},
                "track": {
                    "states": [{"name": "high", "test": "{{ score }} > 100"}]
                },
            }
        }
        result = main.resolve_inputs(rule, definition=definition)
        assert result[0]["track"] is not None
        assert result[0]["validator"] is None


# ========================= _replace_each_placeholders =========================


class TestReplaceEachPlaceholders:
    def test_string_value(self):
        result = main._replace_each_placeholders(
            "https://reddit.com/r/{{sub}}.rss", "sub", "python"
        )
        assert result == "https://reddit.com/r/python.rss"

    def test_dict_dot_notation(self):
        result = main._replace_each_placeholders(
            "https://example.com/{{data.category}}/{{data.type}}",
            "data",
            {"category": "tech", "type": "news"},
        )
        assert result == "https://example.com/tech/news"

    def test_nested_dict(self):
        result = main._replace_each_placeholders(
            "{{d.a.b}}", "d", {"a": {"b": "deep"}}
        )
        assert result == "deep"

    def test_missing_key_unchanged(self):
        result = main._replace_each_placeholders(
            "{{d.missing}}", "d", {"other": "val"}
        )
        assert result == "{{d.missing}}"

    def test_no_placeholder_unchanged(self):
        result = main._replace_each_placeholders(
            "no placeholders here", "var", "value"
        )
        assert result == "no placeholders here"


# ========================= expand_input_each =========================


class TestExpandInputEach:
    def test_basic_expansion(self):
        input_spec = {
            "each": {"var": "sub", "values": ["python", "javascript"]},
            "params": {"feed_url": "https://reddit.com/r/{{sub}}.rss"},
        }
        result = main.expand_input_each(input_spec)
        assert len(result) == 2
        assert result[0]["params"]["feed_url"] == "https://reddit.com/r/python.rss"
        assert result[1]["params"]["feed_url"] == "https://reddit.com/r/javascript.rss"

    def test_with_validator(self):
        input_spec = {
            "each": {"var": "sub", "values": ["a"]},
            "params": {"url": "{{sub}}"},
            "validator": {"match": {"var": "title", "regex": "test"}},
        }
        result = main.expand_input_each(input_spec)
        assert result[0]["validator"] == {"match": {"var": "title", "regex": "test"}}

    def test_with_track(self):
        input_spec = {
            "each": {"var": "sym", "values": ["AAPL"]},
            "params": {"symbol": "{{sym}}"},
            "track": {"value": "{{price}}", "states": []},
        }
        result = main.expand_input_each(input_spec)
        assert result[0]["track"] == {"value": "{{price}}", "states": []}

    def test_dict_values(self):
        input_spec = {
            "each": {
                "var": "data",
                "values": [{"cat": "tech", "lang": "en"}],
            },
            "params": {"url": "https://example.com/{{data.cat}}/{{data.lang}}"},
        }
        result = main.expand_input_each(input_spec)
        assert result[0]["params"]["url"] == "https://example.com/tech/en"


# ========================= Liquid =========================


class TestSetupLiquid:
    def test_registers_commands(self):
        config = {
            "defs": {
                "commands": {
                    "double": {
                        "args": ["n"],
                        "template": "{{ n | times: 2 }}",
                    }
                }
            }
        }
        main.setup_liquid(config)
        result = main.liquid.from_string("{% double 5 %}").render()
        assert "10" in result

    def test_registers_filters(self):
        config = {
            "defs": {
                "filters": {
                    "clean": "replace_regex: '\\s+', ' ' | strip",
                }
            }
        }
        main.setup_liquid(config)
        result = main.liquid.from_string("{{ text | clean }}").render(
            text="  hello   world  "
        )
        assert result == "hello world"

    def test_replace_regex_filter(self):
        main.setup_liquid({"defs": {}})
        result = main.liquid.from_string(
            "{{ text | replace_regex: '\\d+', 'X' }}"
        ).render(text="abc123def456")
        assert result == "abcXdefX"


class TestRenderUrl:
    def test_simple_render(self):
        main.setup_liquid({"defs": {}})
        result = main.render_url("https://example.com/{{q}}", {"q": "test"})
        assert result == "https://example.com/test"


# ========================= Email =========================


class TestRenderEmail:
    def setup_method(self):
        main.setup_liquid({"defs": {}})

    def test_render_subject_and_body(self):
        template = "Items: {{count}}\n{% for item in items %}{{item.title}}\n{% endfor %}"
        items = [{"title": "A"}, {"title": "B"}]
        definition = {"url": "https://example.com"}
        subject, body = main.render_email(
            template, "New: {{count}}", items, {}, definition
        )
        assert subject == "New: 2"
        assert "Items: 2" in body
        assert "A" in body
        assert "B" in body

    def test_items_get_index(self):
        template = "{% for item in items %}{{item.index}},{% endfor %}"
        items = [{"title": "A"}, {"title": "B"}]
        definition = {"url": "https://example.com"}
        _, body = main.render_email(template, "", items, {}, definition)
        assert "1," in body
        assert "2," in body

    def test_nested_items_structure(self):
        template = (
            "{% for group in items %}"
            "[{% for item in group %}{{item.title}},{% endfor %}]"
            "{% endfor %}"
        )
        items = [
            [{"title": "A"}, {"title": "B"}],
            [{"title": "C"}],
        ]
        definition = {"url": "https://example.com/{{page}}"}
        input_groups = [
            {"params": {"page": "p1"}, "search_url": "https://example.com/p1"},
            {"params": {"page": "p2"}, "search_url": "https://example.com/p2"},
        ]
        _, body = main.render_email(
            template, "", items, {}, definition, input_groups=input_groups
        )
        assert "[A,B,]" in body
        assert "[C,]" in body

    def test_nested_items_count_is_total(self):
        template = "Total: {{count}}"
        items = [
            [{"title": "A"}, {"title": "B"}],
            [{"title": "C"}],
        ]
        definition = {"url": "https://example.com/{{page}}"}
        input_groups = [
            {"params": {"page": "p1"}, "search_url": "https://example.com/p1"},
            {"params": {"page": "p2"}, "search_url": "https://example.com/p2"},
        ]
        _, body = main.render_email(
            template, "", items, {}, definition, input_groups=input_groups
        )
        assert "Total: 3" in body

    def test_nested_items_global_index(self):
        template = (
            "{% for group in items %}"
            "{% for item in group %}{{item.index}},{% endfor %}"
            "{% endfor %}"
        )
        items = [
            [{"title": "A"}, {"title": "B"}],
            [{"title": "C"}],
        ]
        definition = {"url": "https://example.com/{{page}}"}
        input_groups = [
            {"params": {"page": "p1"}, "search_url": "https://example.com/p1"},
            {"params": {"page": "p2"}, "search_url": "https://example.com/p2"},
        ]
        _, body = main.render_email(
            template, "", items, {}, definition, input_groups=input_groups
        )
        assert "1,2," in body
        assert "3," in body

    def test_nested_items_search_url(self):
        template = (
            "{% for group in items %}"
            "{% for item in group %}{{item._search_url}}\n{% endfor %}"
            "{% endfor %}"
        )
        items = [
            [{"title": "A"}],
            [{"title": "B"}],
        ]
        definition = {"url": "https://example.com/{{page}}"}
        input_groups = [
            {"params": {"page": "p1"}, "search_url": "https://example.com/p1"},
            {"params": {"page": "p2"}, "search_url": "https://example.com/p2"},
        ]
        _, body = main.render_email(
            template, "", items, {}, definition, input_groups=input_groups
        )
        assert "https://example.com/p1" in body
        assert "https://example.com/p2" in body

    def test_nested_items_input_params(self):
        template = (
            "{% for group in items %}"
            "{% assign first = group | first %}"
            "{{first._input.page}}:"
            "{% for item in group %}{{item.title}},{% endfor %}"
            "{% endfor %}"
        )
        items = [
            [{"title": "A"}],
            [{"title": "B"}],
        ]
        definition = {"url": "https://example.com/{{page}}"}
        input_groups = [
            {"params": {"page": "SEO"}, "search_url": "https://example.com/SEO"},
            {"params": {"page": "UKEN"}, "search_url": "https://example.com/UKEN"},
        ]
        _, body = main.render_email(
            template, "", items, {}, definition, input_groups=input_groups
        )
        assert "SEO:A," in body
        assert "UKEN:B," in body

    def test_nested_items_first_group_empty(self):
        """When the first input has no notify items, the second group's items
        must still get the second input's metadata (not the first's)."""
        template = (
            "{% for group in items %}"
            "{% assign first = group | first %}"
            "{{first._input.page}}:"
            "{% for item in group %}{{item.title}},{% endfor %}"
            "{% endfor %}"
        )
        # Only one group — the second input's items
        items = [
            [{"title": "X"}],
        ]
        definition = {"url": "https://example.com/{{page}}"}
        # Only the matching input_groups entry should be passed
        input_groups = [
            {"params": {"page": "UKEN"}, "search_url": "https://example.com/UKEN"},
        ]
        _, body = main.render_email(
            template, "", items, {}, definition, input_groups=input_groups
        )
        assert "UKEN:X," in body

    def test_flat_items_unchanged_without_input_groups(self):
        template = "{% for item in items %}{{item.title}},{% endfor %}"
        items = [{"title": "A"}, {"title": "B"}]
        definition = {"url": "https://example.com"}
        _, body = main.render_email(template, "", items, {}, definition)
        assert "A,B," in body


class TestLoadTemplate:
    def test_load_existing(self, tmp_mutimon):
        tpl = tmp_mutimon / "templates" / "test"
        tpl.write_text("Hello {{name}}")
        result = main.load_template("./templates/test")
        assert result == "Hello {{name}}"

    def test_load_missing_returns_none(self, tmp_mutimon):
        result = main.load_template("./templates/nonexistent")
        assert result is None


class TestSaveEmailToFile:
    def test_saves_file(self, tmp_mutimon):
        main.save_email_to_file("test-rule", "Test Subject", "Test body")
        email_file = tmp_mutimon / "data" / "emails" / "test-rule.txt"
        assert email_file.exists()
        content = email_file.read_text()
        assert "Test Subject" in content
        assert "Test body" in content


class TestSendEmail:
    def test_sends_via_smtp(self, sample_config):
        with mock.patch("mutimon.main.smtplib.SMTP") as mock_smtp:
            mock_server = mock.MagicMock()
            mock_smtp.return_value.__enter__ = mock.Mock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = mock.Mock(return_value=False)
            main.send_email(sample_config, "user@test.com", "Subject", "Body")
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once()
            mock_server.send_message.assert_called_once()

    def test_raises_on_failure(self, sample_config):
        with mock.patch("mutimon.main.smtplib.SMTP") as mock_smtp:
            mock_smtp.side_effect = Exception("Connection refused")
            with pytest.raises(Exception, match="Connection refused"):
                main.send_email(sample_config, "user@test.com", "Sub", "Body")


# ========================= ShouldInclude =========================


class TestShouldInclude:
    def test_no_filter(self):
        el = BeautifulSoup("<div>test</div>", "html.parser").select_one("div")
        assert main.should_include(el, None) is True
        assert main.should_include(el, {}) is True

    def test_filter_passes(self):
        html = '<div><span class="date">Today</span></div>'
        el = BeautifulSoup(html, "html.parser").select_one("div")
        assert main.should_include(el, {"selector": ".date"}) is True

    def test_filter_excludes_missing(self):
        html = "<div><span>No date</span></div>"
        el = BeautifulSoup(html, "html.parser").select_one("div")
        assert main.should_include(el, {"selector": ".date"}) is False

    def test_exclude_class(self):
        html = '<div><span class="date closed">Today</span></div>'
        el = BeautifulSoup(html, "html.parser").select_one("div")
        spec = {"selector": ".date", "exclude_class": "closed"}
        assert main.should_include(el, spec) is False
