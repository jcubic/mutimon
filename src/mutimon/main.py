#!/usr/bin/env python3

"""
Mutimon — config-driven web scraper and email notifier.

Reads scraping rules from ~/.mutimon/config.json, extracts data from websites
using CSS selectors, detects new items, and sends email notifications using
Liquid templates. Processes all rules defined in config on each run.

Usage:
    mon                       # process rules whose schedule is due
    mon --force               # ignore schedules and run all rules
    mon --force <rule> ...    # ignore schedule and run specific rules
    mon --dry-run             # fetch and display data without sending emails
    mon --init                # seed state for all rules without sending emails
    mon --init <rule> ...     # seed state for specific rules
    mon --save-email          # save emails to file instead of sending
    mon --validate            # validate config and exit
    mon --verbose             # show detailed progress output
    mon -q                    # run silently (for cron)

Designed to run as a daily cron job.
"""

# === Standard library imports (always available) ===
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import smtplib
import sys
import time
import traceback
from datetime import date, datetime
from email.message import EmailMessage
from urllib.parse import urljoin

# ========================= PATHS =========================
MUTIMON_DIR = os.path.expanduser("~/.mutimon")
CONFIG_FILE = os.path.join(MUTIMON_DIR, "config.json")
TEMPLATES_DIR = os.path.join(MUTIMON_DIR, "templates")
DATA_DIR = os.path.join(MUTIMON_DIR, "data")
SKELETON_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "skeleton")
LOGS_DIR = os.path.join(MUTIMON_DIR, "logs")
SECRETS_FILE = os.path.join(MUTIMON_DIR, "secrets.json")
AUTH_DIR = os.path.join(DATA_DIR, ".auth")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

verbose = False
_secrets = {}


class UndefinedVariableError(Exception):
    pass


def log(msg):
    """Print a message only when --verbose is enabled."""
    if verbose:
        print(msg)


def info(msg):
    """Print an essential message always (unless --quiet)."""
    print(msg)


def rule_log(rule_name, msg):
    """Append a timestamped message to ~/.mutimon/logs/<rule_name>.log."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_file = os.path.join(LOGS_DIR, f"{rule_name}.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")


# =========================================================


def send_error_email(subject, body):
    """
    Send an error notification using only stdlib.

    Reads SMTP config and recipient emails directly from config.json.
    This function must not depend on any third-party library so it works
    even when the error is a missing dependency.
    """
    try:
        if not os.path.exists(CONFIG_FILE):
            return

        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)

        server_config = config.get("email", {}).get("server", {})
        host = server_config.get("host")
        port = server_config.get("port", 587)
        sender = server_config.get("email")
        password = server_config.get("password")

        if password and "{{" in password:
            try:
                with open(SECRETS_FILE, "r", encoding="utf-8") as sf:
                    secrets = json.load(sf)
                password = secrets.get("email", {}).get("password")
            except (json.JSONDecodeError, OSError, FileNotFoundError):
                password = None
            if not password:
                return

        if not all([host, sender, password]):
            return

        # Collect unique recipient emails from all rules
        recipients = set()
        for rule in config.get("rules", []):
            email = rule.get("email")
            if email:
                recipients.add(email)
        if not recipients:
            recipients.add(sender)

        for recipient in recipients:
            msg = EmailMessage()
            msg["From"] = f"Mutimon <{sender}>"
            msg["To"] = recipient
            msg["Subject"] = subject
            msg.set_content(body)

            with smtplib.SMTP(host, port, timeout=30) as server:
                server.starttls()
                server.login(sender, password)
                server.send_message(msg)

    except Exception:
        # If sending the error email itself fails, just print — don't recurse
        traceback.print_exc()


# === Third-party imports ===
try:
    import requests
    import jmespath
    from liquid import Environment as LiquidEnvironment
    from liquid import Tag as LiquidTag
    from liquid.ast import Node as LiquidNode
    from liquid.token import TOKEN_TAG, TOKEN_EXPRESSION
    from expression import Expression
    from babel import Locale
    from babel.numbers import parse_decimal
    from croniter import croniter
    from bs4 import BeautifulSoup
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - exercised only when a dependency is missing
    tb = traceback.format_exc()
    print(tb, file=sys.stderr)
    send_error_email(
        "[mutimon] Missing dependency",
        f"Mutimon failed to start at {datetime.now()}.\n\n{tb}",
    )
    sys.exit(1)


def init_config():
    """Create ~/.mutimon with skeleton files if config is missing."""
    if os.path.exists(CONFIG_FILE):
        return

    print(f"Config not found at {CONFIG_FILE}")
    print(f"Creating skeleton configuration in {MUTIMON_DIR}...")

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    os.makedirs(AUTH_DIR, exist_ok=True)

    # Create empty secrets.json if it doesn't exist
    if not os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, "w", encoding="utf-8") as f:
            json.dump({"email": {"password": ""}}, f, ensure_ascii=False, indent=2)

    if os.path.isdir(SKELETON_DIR):
        # Copy skeleton config.json
        src_config = os.path.join(SKELETON_DIR, "config.json")
        if os.path.exists(src_config):
            shutil.copy2(src_config, CONFIG_FILE)

        # Copy skeleton templates
        src_templates = os.path.join(SKELETON_DIR, "templates")
        if os.path.isdir(src_templates):
            for name in os.listdir(src_templates):
                src = os.path.join(src_templates, name)
                dst = os.path.join(TEMPLATES_DIR, name)
                if os.path.isfile(src) and not os.path.exists(dst):
                    shutil.copy2(src, dst)
    else:
        # Fallback: write a minimal config inline
        minimal = {
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
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(minimal, f, ensure_ascii=False, indent=2)

    print(f"Done. Edit {CONFIG_FILE} to configure your scraping rules.")
    sys.exit(0)


def is_skeleton_config():
    """Check if the current config is unchanged from the skeleton default."""
    src_config = os.path.join(SKELETON_DIR, "config.json")
    if not os.path.exists(src_config) or not os.path.exists(CONFIG_FILE):
        return False
    with open(src_config, "r", encoding="utf-8") as f:
        skeleton = f.read()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        current = f.read()
    return skeleton == current


def print_setup_guide():
    """Print a quick setup guide when the config hasn't been customized yet."""
    print("It looks like you haven't configured Mutimon yet.\n")
    print("Quick setup:")
    print(f"  1. Open {CONFIG_FILE}")
    print("  2. Set your SMTP server credentials in the \"email\" section")
    print("  3. Add a scraping definition to \"defs\" (URL + CSS selectors)")
    print("  4. Add a rule to \"rules\" (schedule, template, recipient)")
    print("  5. Create a Liquid template in ~/.mutimon/templates/")
    print("  6. Run: mon --validate")
    print("  7. Test: mon --dry-run --force")
    print("  8. Add a cron job: (crontab -l 2>/dev/null; mon --cron) | crontab -")
    print()
    print("TIP: You can use an AI assistant to add new websites:")
    print("     claude -p \"$(mon --ai-guide) Add https://example.com to mon\"")
    print()
    print("     Run 'mon --ai-guide' to print the AI instruction guide.")
    sys.exit(0)


def _hash_dict(d):
    """Return a SHA-256 hex digest of a dict's canonical JSON."""
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()


def _load_skeleton_email_server():
    """Load the email.server object from the skeleton config."""
    src_config = os.path.join(SKELETON_DIR, "config.json")
    if not os.path.exists(src_config):
        return None
    with open(src_config, "r", encoding="utf-8") as f:
        return json.load(f).get("email", {}).get("server", {})


def load_config():
    """Load the configuration file."""
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_secrets():
    """Load secrets.json if it exists. Returns empty dict otherwise."""
    if not os.path.exists(SECRETS_FILE):
        return {}
    try:
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def liquid_context(params, auth_data=None):
    """Build Liquid rendering context with secrets and auth namespaces."""
    ctx = dict(params)
    ctx["secret"] = _secrets
    if auth_data:
        ctx["auth"] = auth_data
    return ctx


def validate_config(config, validate_only=False):
    """
    Validate config against the JSON Schema, then check cron expressions,
    CSS selectors, and JMESPath paths for syntax errors.

    Sends an error email listing all validation errors and exits
    if the config is invalid.
    """
    schema_file = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "config.schema.json"
    )
    if not os.path.exists(schema_file):
        print(
            f"Warning: Schema file not found at {schema_file}, skipping validation.",
            file=sys.stderr,
        )
        return

    with open(schema_file, "r", encoding="utf-8") as f:
        schema = json.load(f)

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(config))

    if errors:
        _report_validation_errors(errors, validate_only)

    # Additional syntax checks beyond JSON Schema
    syntax_errors = []
    syntax_errors.extend(_validate_cron_expressions(config))
    syntax_errors.extend(_validate_css_selectors(config))
    syntax_errors.extend(_validate_jmespath_paths(config))
    syntax_errors.extend(_validate_regex_patterns(config))

    if syntax_errors:
        _report_validation_errors(syntax_errors, validate_only)

    for warning in _collect_deprecation_warnings(config):
        print(f"Warning: {warning}", file=sys.stderr)


def _collect_deprecation_warnings(config):
    """Find deprecated config patterns and return human-readable warning strings."""
    warnings = []

    def check_validator(validator, path):
        if isinstance(validator, list):
            for i, v in enumerate(validator):
                check_validator(v, f"{path}[{i}]")
            return
        if not isinstance(validator, dict) or "@id" in validator:
            return
        match = validator.get("match")
        if not match:
            return
        matches = match if isinstance(match, list) else [match]
        for i, m in enumerate(matches):
            if isinstance(m, dict) and "regex" in m:
                match_path = (
                    f"{path}.match[{i}]" if isinstance(match, list) else f"{path}.match"
                )
                warnings.append(
                    f"[{match_path}] 'match.regex' is deprecated; "
                    f"use 'test' with =~ operator instead "
                    f"(e.g. {{\"test\": \"title =~ /pattern/\"}})"
                )

    for name, v in config.get("defs", {}).get("validators", {}).items():
        check_validator(v, f"defs.validators.{name}")

    for rule in config.get("rules", []):
        rule_name = rule.get("name", "?")
        input_spec = rule.get("input")
        if isinstance(input_spec, dict) and "each" not in input_spec:
            input_spec = [input_spec]
        elif isinstance(input_spec, dict) and "each" in input_spec:
            v = input_spec.get("validator")
            if v:
                check_validator(v, f"rules.{rule_name}.input.validator")
            input_spec = None
        if isinstance(input_spec, list):
            for i, entry in enumerate(input_spec):
                v = entry.get("validator")
                if v:
                    suffix = f"[{i}]" if len(input_spec) > 1 else ""
                    check_validator(
                        v, f"rules.{rule_name}.input{suffix}.validator"
                    )

    return warnings


def _report_validation_errors(errors, validate_only=False):
    """Format and report validation errors, then exit."""
    lines = [f"Config validation failed with {len(errors)} error(s):\n"]
    for i, err in enumerate(errors, 1):
        if isinstance(err, str):
            lines.append(f"  {i}. {err}")
        else:
            path = ".".join(str(p) for p in err.absolute_path) or "(root)"
            lines.append(f"  {i}. [{path}] {err.message}")

    msg = "\n".join(lines)
    print(msg, file=sys.stderr)
    if not validate_only:
        send_error_email(
            "[mutimon] Invalid configuration",
            f"Config at {CONFIG_FILE} is invalid.\n\n{msg}",
        )
    sys.exit(1)


def _validate_cron_expressions(config):
    """Check all schedule cron expressions for syntax errors."""
    errors = []
    for rule in config.get("rules", []):
        schedule = rule.get("schedule")
        if not schedule:
            continue
        expressions = schedule if isinstance(schedule, list) else [schedule]
        for expr in expressions:
            if not croniter.is_valid(expr):
                name = rule.get("name", "?")
                errors.append(
                    f"[rules.{name}.schedule] Invalid cron expression: '{expr}'"
                )
    return errors


def _validate_css_selectors(config):
    """Check all CSS selectors for syntax errors."""
    errors = []
    soup = BeautifulSoup("<div></div>", "html.parser")

    def check_selector(selector, path):
        try:
            soup.select(selector)
        except Exception as e:
            errors.append(f"[{path}] Invalid CSS selector '{selector}': {e}")

    for def_name, definition in config.get("defs", {}).items():
        if def_name == "commands":
            continue
        query = definition.get("query", {})
        if query.get("selector"):
            check_selector(query["selector"], f"defs.{def_name}.query.selector")
        for expect_sel in query.get("expect", []):
            check_selector(expect_sel, f"defs.{def_name}.query.expect")
        for reject_sel in query.get("reject", []):
            check_selector(reject_sel, f"defs.{def_name}.query.reject")
        filter_spec = query.get("filter", {})
        if filter_spec.get("selector"):
            check_selector(
                filter_spec["selector"], f"defs.{def_name}.query.filter.selector"
            )
        pagination = definition.get("pagination", {})
        if pagination.get("selector"):
            check_selector(
                pagination["selector"], f"defs.{def_name}.pagination.selector"
            )
        for var_name, var_spec in query.get("variables", {}).items():
            sel = var_spec.get("selector")
            if sel and sel != ":self":
                check_selector(
                    sel, f"defs.{def_name}.query.variables.{var_name}.selector"
                )
            var_path = f"defs.{def_name}.query.variables.{var_name}"
            for i, step in enumerate(var_spec.get("find", [])):
                if len(step) >= 2 and step[0] in ("select", "until"):
                    check_selector(step[1], f"{var_path}.find[{i}]")
            for i, step in enumerate(var_spec.get("transform", [])):
                if len(step) >= 2 and step[0] in ("remove", "remove_after"):
                    check_selector(step[1], f"{var_path}.transform[{i}]")
    return errors


def _validate_jmespath_paths(config):
    """Check all JMESPath path expressions for syntax errors."""
    errors = []
    for def_name, definition in config.get("defs", {}).items():
        if def_name == "commands":
            continue
        query = definition.get("query", {})
        for var_name, var_spec in query.get("variables", {}).items():
            value = var_spec.get("value", {})
            if value.get("parse") != "json":
                continue
            json_query = value.get("query", {})
            path = json_query.get("path")
            if path:
                # Strip Liquid variables before checking JMESPath syntax
                test_path = re.sub(r"\{\{[^}]*\}\}", "0", path)
                try:
                    jmespath.compile(test_path)
                except jmespath.exceptions.ParseError as e:
                    errors.append(
                        f"[defs.{def_name}.query.variables.{var_name}"
                        f".value.query.path] Invalid JMESPath: {e}"
                    )
            for sub_var, sub_spec in json_query.get("variables", {}).items():
                sub_path = sub_spec.get("path")
                if sub_path:
                    try:
                        jmespath.compile(sub_path)
                    except jmespath.exceptions.ParseError as e:
                        errors.append(
                            f"[defs.{def_name}.query.variables.{var_name}"
                            f".value.query.variables.{sub_var}.path] "
                            f"Invalid JMESPath: {e}"
                        )
    return errors


def _validate_regex_patterns(config):
    """Check all regex patterns for syntax errors."""
    errors = []

    def check_regex(pattern, path):
        try:
            re.compile(pattern)
        except re.error as e:
            errors.append(f"[{path}] Invalid regex '{pattern}': {e}")

    for def_name, definition in config.get("defs", {}).items():
        if def_name in ("commands", "filters", "validators"):
            continue
        query = definition.get("query", {})
        id_spec = query.get("id", {})
        if id_spec.get("regex"):
            check_regex(id_spec["regex"], f"defs.{def_name}.query.id.regex")
        for var_name, var_spec in query.get("variables", {}).items():
            value = var_spec.get("value", {})
            if value.get("regex"):
                check_regex(
                    value["regex"],
                    f"defs.{def_name}.query.variables.{var_name}.value.regex",
                )

    def check_validator(validator, path):
        if isinstance(validator, list):
            for i, v in enumerate(validator):
                check_validator(v, f"{path}[{i}]")
            return
        if not isinstance(validator, dict):
            return
        if "@id" in validator:
            return
        match = validator.get("match")
        if match:
            matches = match if isinstance(match, list) else [match]
            for i, m in enumerate(matches):
                if isinstance(m, dict) and m.get("regex"):
                    match_path = f"{path}.match[{i}]" if isinstance(match, list) else f"{path}.match"
                    check_regex(m["regex"], f"{match_path}.regex")

    validators_defs = config.get("defs", {}).get("validators", {})
    for name, v in validators_defs.items():
        check_validator(v, f"defs.validators.{name}")

    for rule in config.get("rules", []):
        rule_name = rule.get("name", "?")
        input_spec = rule.get("input")
        if isinstance(input_spec, dict) and "each" not in input_spec:
            input_spec = [input_spec]
        elif isinstance(input_spec, dict) and "each" in input_spec:
            v = input_spec.get("validator")
            if v:
                check_validator(v, f"rules.{rule_name}.input.validator")
            input_spec = None
        if isinstance(input_spec, list):
            for i, entry in enumerate(input_spec):
                v = entry.get("validator")
                if v:
                    check_validator(v, f"rules.{rule_name}.input[{i}].validator")

    return errors


def load_state(rule_name):
    """Load state for a specific rule from ~/.mutimon/data/<rule_name>."""
    state_file = os.path.join(DATA_DIR, rule_name)
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_state(rule_name, items):
    """Save state for a specific rule to ~/.mutimon/data/<rule_name>."""
    state_file = os.path.join(DATA_DIR, rule_name)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def load_last_run(rule_name):
    """Load the last run timestamp for a rule."""
    run_file = os.path.join(DATA_DIR, f".lastrun_{rule_name}")
    if os.path.exists(run_file):
        try:
            with open(run_file, "r", encoding="utf-8") as f:
                return datetime.fromisoformat(f.read().strip())
        except (ValueError, IOError):
            pass
    return None


def save_last_run(rule_name):
    """Save the current timestamp as last run for a rule."""
    run_file = os.path.join(DATA_DIR, f".lastrun_{rule_name}")
    with open(run_file, "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat())


def should_run_now(rule):
    """
    Check if a rule should run based on its cron schedule.

    The script is invoked periodically by system cron (e.g. every 5 minutes
    or every hour). We check if the current time (truncated to the start of
    the minute) matches the rule's cron expression. The last-run timestamp
    prevents duplicate runs within the same minute window.

    Returns True if:
      - No schedule is defined (always run)
      - The current minute matches the cron expression AND the rule
        hasn't already run in this minute window
    """
    schedule = rule.get("schedule")
    if not schedule:
        return True

    # Truncate to the start of the current minute
    now = datetime.now().replace(second=0, microsecond=0)

    # Support string or array of cron expressions (any match = run)
    schedules = schedule if isinstance(schedule, list) else [schedule]
    if not any(croniter.match(s, now) for s in schedules):
        return False

    # Prevent duplicate runs within the same minute
    rule_name = rule["name"]
    last_run = load_last_run(rule_name)
    if last_run is not None:
        last_run_minute = last_run.replace(second=0, microsecond=0)
        if last_run_minute >= now:
            return False

    return True


liquid = LiquidEnvironment()


class CommandNode(LiquidNode):
    """Node that renders a command template with bound arguments."""

    __slots__ = ("_env", "template_str", "arg_names", "raw_args")

    def __init__(self, token, env, template_str, arg_names, raw_args):
        super().__init__(token)
        self._env = env
        self.template_str = template_str
        self.arg_names = arg_names
        self.raw_args = raw_args

    def render_to_output(self, context, buffer):
        kwargs = {}
        for name, (kind, raw) in zip(self.arg_names, self.raw_args):
            if kind == "word":
                kwargs[name] = context.resolve(raw, default=raw)
            elif kind == "integer":
                kwargs[name] = int(raw)
            elif kind == "float":
                kwargs[name] = float(raw)
            else:
                kwargs[name] = raw
        tpl = self._env.from_string(self.template_str)
        result = tpl.render(**kwargs)
        buffer.write(result)
        return len(result)


def make_command_tag(cmd_name, template_str, arg_names):
    """Create a Liquid Tag subclass for a named command."""

    class DynamicCommandTag(LiquidTag):
        name = cmd_name
        block = False

        def parse(self, stream):
            token = stream.eat(TOKEN_TAG)
            raw_args = []
            if stream.current.kind == TOKEN_EXPRESSION:
                inner = stream.into_inner(tag=token)
                while inner.current != inner.eof:
                    t = inner.next()
                    raw_args.append((t.kind, t.value))
            return CommandNode(token, self.env, template_str, arg_names, raw_args)

    return DynamicCommandTag


def replace_regex(value, pattern, replacement):
    """Built-in filter: regex replacement on a string value."""
    return re.sub(pattern, replacement, str(value))


def liquid_html2text(value):
    """Liquid filter: convert HTML to plain text preserving code blocks."""
    import html2text
    converter = html2text.HTML2Text()
    converter.body_width = 0
    return converter.handle(str(value)).strip()


def make_filter(expression, env):
    """Create a Liquid filter function from a filter expression string.

    The expression uses standard Liquid filter syntax, e.g.:
        "replace_regex: '\\s+', ' ' | strip"

    Compiled once into a template: {{ __value__ | <expression> }}
    """
    template = env.from_string("{{ __value__ | " + expression + " }}")

    def filter_func(value):
        return template.render(__value__=value)

    return filter_func


def setup_liquid(config):
    """Register custom command tags and filters from config into the Liquid environment."""
    global liquid
    commands = config.get("defs", {}).get("commands", {})
    for cmd_name, cmd_def in commands.items():
        template_str = cmd_def["template"]
        arg_names = cmd_def.get("args", [])
        liquid.add_tag(make_command_tag(cmd_name, template_str, arg_names))
    # Register built-in base filters
    liquid.add_filter("replace_regex", replace_regex)
    liquid.add_filter("html2text", liquid_html2text)
    # Register user-defined filters from config (Liquid filter expressions)
    filters = config.get("defs", {}).get("filters", {})
    for filter_name, filter_expr in filters.items():
        liquid.add_filter(filter_name, make_filter(filter_expr, liquid))


def render_url(url_template, params, auth_data=None):
    """Render a URL template with Liquid params, secrets, and auth."""
    return liquid.from_string(url_template).render(**liquid_context(params, auth_data))


def detect_language(html, response_headers=None):
    """
    Detect the page language from HTML lang attribute or Content-Language header.
    Returns a babel Locale or defaults to en.
    """
    # Try <html lang="...">
    soup = BeautifulSoup(html[:2000], "html.parser")
    html_tag = soup.find("html")
    lang = str(html_tag.get("lang", "")) if html_tag else ""

    # Fall back to Content-Language header
    if not lang and response_headers:
        lang = response_headers.get("Content-Language", "")

    # Clean up (e.g. "en-US" -> "en_US", "pl" -> "pl")
    lang = lang.strip().split(",")[0].strip() if lang else "en"

    try:
        return Locale.parse(lang.replace("-", "_"))
    except Exception:
        return Locale.parse("en")


def fetch_url(url, method="GET", headers=None, body=None, user_agent=None):
    """Fetch a URL with configurable method, headers, and body. Returns Response."""
    req_headers = {"User-Agent": user_agent or USER_AGENT}
    if headers:
        req_headers.update(headers)
    kwargs = {"headers": req_headers, "timeout": 30}
    if method.upper() == "POST" and body is not None:
        kwargs["json"] = body
    resp = requests.request(method.upper(), url, **kwargs)
    resp.raise_for_status()
    return resp


def fetch_page(url, user_agent=None, is_xml=False, method="GET", headers=None, body=None):
    """Fetch a single page and return its HTML and detected locale."""
    resp = fetch_url(url, method=method, headers=headers, body=body, user_agent=user_agent)
    if is_xml:
        locale = Locale.parse("en")
    else:
        locale = detect_language(resp.text, resp.headers)
    return resp.text, locale


def fetch_json(url, method="GET", headers=None, body=None, user_agent=None):
    """Fetch URL and return parsed JSON."""
    resp = fetch_url(url, method=method, headers=headers, body=body, user_agent=user_agent)
    return resp.json()


def parse_json_items(json_data, query_spec):
    """Extract items from parsed JSON using JMESPath. Returns list of dicts."""
    result = query_json(json_data, query_spec, {})
    if isinstance(result, dict):
        items = [result]
    elif isinstance(result, list):
        items = result
    else:
        return []
    id_spec = query_spec.get("id")
    for item in items:
        if id_spec and not item.get("id"):
            item["id"] = extract_id(item, id_spec)
    return items


# ========================= Auth =========================


def _auth_cache_path(def_name):
    """Path to cached auth data for a definition."""
    return os.path.join(AUTH_DIR, f"{def_name}.json")


def _load_cached_auth(def_name):
    """Load cached auth tokens. Returns dict or None."""
    path = _auth_cache_path(def_name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_cached_auth(def_name, auth_data):
    """Save auth tokens to cache."""
    os.makedirs(AUTH_DIR, exist_ok=True)
    path = _auth_cache_path(def_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(auth_data, f, ensure_ascii=False, indent=2)


def _render_liquid_dict(d, ctx):
    """Render all string values in a dict through Liquid."""
    rendered = {}
    for k, v in d.items():
        if isinstance(v, str):
            rendered[k] = liquid.from_string(v).render(**ctx)
        else:
            rendered[k] = v
    return rendered


def _extract_auth_values(resp, extract_spec, fmt="json"):
    """Extract auth values from a response based on extract spec."""
    auth_data = {}
    # Parse body based on format
    body_data = None
    if fmt == "json":
        try:
            body_data = resp.json()
        except Exception:
            body_data = {}
    elif fmt in ("html", "xml"):
        body_data = resp.text

    for key, spec in extract_spec.items():
        source = spec.get("source", "body")
        if source == "body":
            if isinstance(body_data, dict) and "path" in spec:
                auth_data[key] = jmespath.search(spec["path"], body_data)
            elif isinstance(body_data, str) and "selector" in spec:
                soup = BeautifulSoup(body_data, "html.parser")
                el = soup.select_one(spec["selector"])
                if el:
                    auth_data[key] = el.get_text(strip=True)
        elif source == "cookie":
            auth_data[key] = resp.cookies.get(spec.get("name", ""))
        elif source == "header":
            auth_data[key] = resp.headers.get(spec.get("name", ""))
    return auth_data


def perform_auth_request(step_spec, params, existing_auth=None):
    """Execute an auth step (login or refresh). Returns extracted auth data."""
    ctx = liquid_context(params, existing_auth)
    url = liquid.from_string(step_spec["url"]).render(**ctx)
    method = step_spec.get("method", "POST")
    fmt = step_spec.get("format", "json")

    step_headers = _render_liquid_dict(step_spec.get("headers", {}), ctx)
    step_body = _render_liquid_dict(step_spec.get("body", {}), ctx) if step_spec.get("body") else None

    resp = fetch_url(url, method=method, headers=step_headers, body=step_body)

    extract_spec = step_spec.get("extract", {})
    auth_data = _extract_auth_values(resp, extract_spec, fmt)

    # Merge with existing auth (for refresh — keep values not re-extracted)
    if existing_auth:
        merged = dict(existing_auth)
        merged.update(auth_data)
        return merged
    return auth_data


def resolve_auth(definition, params, def_name=None):
    """
    Resolve auth for a definition. Returns (headers_dict, cookies_dict, auth_data).
    Handles caching, refresh, and login flows.
    """
    auth_spec = definition.get("auth")
    if not auth_spec:
        return {}, {}, None

    apply_spec = auth_spec.get("apply", {})

    # Try cached auth first
    cached = _load_cached_auth(def_name) if def_name else None
    if cached:
        ctx = liquid_context(params, cached)
        headers = _render_liquid_dict(apply_spec.get("headers", {}), ctx)
        cookies = _render_liquid_dict(apply_spec.get("cookies", {}), ctx)
        return headers, cookies, cached

    # No cache — login
    login_spec = auth_spec.get("login")
    if not login_spec:
        return {}, {}, None

    auth_data = perform_auth_request(login_spec, params)
    if def_name:
        _save_cached_auth(def_name, auth_data)

    ctx = liquid_context(params, auth_data)
    headers = _render_liquid_dict(apply_spec.get("headers", {}), ctx)
    cookies = _render_liquid_dict(apply_spec.get("cookies", {}), ctx)
    return headers, cookies, auth_data


def retry_auth(auth_spec, params, def_name, cached_auth):
    """Handle 401 — try refresh, then re-login. Returns (headers, cookies, auth_data) or None."""
    # Try refresh first
    refresh_spec = auth_spec.get("refresh")
    if refresh_spec and cached_auth:
        try:
            auth_data = perform_auth_request(refresh_spec, params, cached_auth)
            if def_name:
                _save_cached_auth(def_name, auth_data)
            apply_spec = auth_spec.get("apply", {})
            ctx = liquid_context(params, auth_data)
            headers = _render_liquid_dict(apply_spec.get("headers", {}), ctx)
            cookies = _render_liquid_dict(apply_spec.get("cookies", {}), ctx)
            return headers, cookies, auth_data
        except Exception:
            pass  # Refresh failed, try re-login

    # Re-login
    login_spec = auth_spec.get("login")
    if not login_spec:
        return None
    try:
        auth_data = perform_auth_request(login_spec, params)
        if def_name:
            _save_cached_auth(def_name, auth_data)
        apply_spec = auth_spec.get("apply", {})
        ctx = liquid_context(params, auth_data)
        headers = _render_liquid_dict(apply_spec.get("headers", {}), ctx)
        cookies = _render_liquid_dict(apply_spec.get("cookies", {}), ctx)
        return headers, cookies, auth_data
    except Exception:
        return None


def extract_value(element, value_spec, default=None, locale=None):
    """
    Extract a value from a BeautifulSoup element based on the value spec.

    value_spec: {
      "type": "text" | "attribute",
      "name": "href",       # only for type=attribute
      "regex": "pattern",   # optional: extract group(1) from value
      "prefix": "https://..." # optional: prepend to final value
    }
    """
    if element is None:
        return default

    if value_spec["type"] == "text":
        # Use separator=' ' to preserve word boundaries when text spans inline
        # elements (e.g. <a>foo</a> bar would otherwise become "foobar")
        raw = element.get_text(separator=" ", strip=True)
    elif value_spec["type"] == "html":
        raw = element.decode_contents()
    elif value_spec["type"] == "attribute":
        raw = element.get(value_spec["name"], "")
        if raw is None:
            raw = ""
    else:
        return default

    if not raw:
        return default if default is not None else raw

    # Apply regex extraction if specified
    regex = value_spec.get("regex")
    if regex:
        match = re.search(regex, raw)
        if match:
            raw = match.group(1) if match.lastindex else match.group(0)
        else:
            return default if default is not None else ""

    # Apply prefix if specified
    prefix = value_spec.get("prefix")
    if prefix:
        if value_spec.get("parse") == "url":
            raw = urljoin(prefix, raw)
        else:
            raw = prefix + raw

    # Apply type parsing if specified
    parse = value_spec.get("parse")
    if parse == "number":
        raw = parse_number(raw)
    elif parse == "money":
        raw = parse_money(raw, locale=locale)
    elif parse == "date":
        raw = parse_date(raw, locale=locale)
    elif parse == "list":
        delimiter = value_spec.get("delimiter", r"\s*,\s*")
        raw = [s for s in re.split(delimiter, raw) if s]
    elif parse == "json":
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return default if default is not None else {}

    return raw


_json_cache = {}


def query_json(json_data, query_spec, item_data):
    """Query parsed JSON data using JMESPath and extract variables.

    query_spec: {
      "type": "list" | "single",
      "path": "data.items[?id == `{{id}}`]",  # JMESPath (Liquid-rendered)
      "variables": {
        "city": { "path": "displayWorkplace" },
        "url":  { "path": "offerAbsoluteUri" }
      }
    }

    Returns a list of dicts (type=list) or a flat dict (type=single).
    If path is omitted, the root JSON object is used.
    """
    query_type = query_spec.get("type", "single")
    empty = [] if query_type == "list" else {}

    # Navigate with JMESPath (Liquid-rendered for variable interpolation)
    jmespath_expr = query_spec.get("path")
    if jmespath_expr:
        rendered_path = liquid.from_string(jmespath_expr).render(**item_data)
        result = jmespath.search(rendered_path, json_data)
    else:
        result = json_data

    if result is None:
        return empty

    variables = query_spec.get("variables", {})

    def extract_from_entry(entry):
        data = {}
        for var_name, var_spec in variables.items():
            var_path = var_spec.get("path")
            if var_path:
                data[var_name] = jmespath.search(var_path, entry)
            else:
                data[var_name] = entry
            if data[var_name] is None:
                data[var_name] = ""
        return data

    if query_type == "list":
        if not isinstance(result, list):
            result = [result]
        return [extract_from_entry(entry) for entry in result]
    else:
        if isinstance(result, list):
            result = result[0] if result else {}
        return extract_from_entry(result)


def parse_number(value):
    """
    Parse a plain numeric string into a float.

    Handles integers and floats with optional thousands separators.
    Does NOT handle currency symbols — use parse="money" for prices.

    Examples: "1234", "1,234", "3.14", "-0.84", "+5.2"
    """
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    # Strip non-numeric chars except digits, dot, comma, minus, plus
    s = re.sub(r"[^\d,.\-+]", "", s)
    # Remove commas (thousands separators in plain numbers)
    s = s.replace(",", "")
    if not s:
        return 0
    try:
        result = float(s)
        if result == int(result):
            return int(result)
        return result
    except (ValueError, TypeError):
        return 0


def parse_money(value, locale=None):
    """
    Parse a monetary string into a float using locale-aware parsing via babel.

    Strips currency symbols, whitespace, and percentage signs before parsing.
    Uses the page's detected locale to correctly interpret decimal and
    thousands separators (e.g. "$70,528.40" in en vs "11,8000 zł" in pl).

    Falls back to a basic parser if babel fails.
    """
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    # Strip leading non-numeric chars (currency symbols like $, €)
    s = re.sub(r"^[^\d\-+]+", "", s)
    # Strip trailing non-numeric chars (currency codes like zł, %, USD)
    s = re.sub(r"[^\d,.]+$", "", s)
    # Replace non-breaking spaces with regular spaces
    s = s.replace("\xa0", " ").strip()

    if not s:
        return 0.0

    if locale is None:
        locale = Locale.parse("en")

    try:
        return float(parse_decimal(s, locale=locale))
    except Exception:
        # Fallback: strip everything except digits, dot, minus
        s = re.sub(r"[^\d.\-]", "", s)
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0


_month_maps = {}


def _month_map(locale):
    """Build a lowercase month-name -> number map for a babel locale.

    Includes both 'format' and 'stand-alone' contexts at 'abbreviated' and
    'wide' widths, so localized forms all resolve — e.g. the Polish genitive
    "lipca" (format/wide, used in section headings), the abbreviation "lip"
    (format/abbreviated, used in signatures), and the nominative "lipiec"
    (stand-alone/wide) all map to 7.
    """
    key = str(locale)
    cached = _month_maps.get(key)
    if cached is not None:
        return cached
    months = {}
    for context in ("format", "stand-alone"):
        for width in ("abbreviated", "wide"):
            try:
                names = locale.months[context][width]
            except Exception:
                continue
            for num, name in names.items():
                months[str(name).lower().rstrip(".")] = num
    _month_maps[key] = months
    return months


def parse_date(value, locale=None):
    """
    Parse a (possibly localized) date string into an ISO 8601 date string
    ("YYYY-MM-DD"), using the page's detected locale for month names.

    Handles "<day> <month-name> [<year>]" with localized month names, such as
    the Polish section heading "10 lipca" (year omitted -> current year) and
    the signature "23:52, 10 lip 2026 (CEST)". Falls back to dateutil for
    ISO / English / numeric formats. Returns the original string unchanged if
    no date can be extracted, so unparseable values stay visible for debugging.
    """
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return s

    if locale is None:
        locale = Locale.parse("en")

    # Try "<day> <localized month name> [<year>]".
    months = _month_map(locale)
    match = re.search(r"(\d{1,2})\s+([^\W\d_]+)\.?(?:\s+(\d{4}))?", s, re.UNICODE)
    if match:
        month = months.get(match.group(2).lower())
        if month:
            day = int(match.group(1))
            year = int(match.group(3)) if match.group(3) else datetime.now().year
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                pass

    # Fallback: dateutil handles ISO / English / numeric formats.
    try:
        from dateutil import parser as date_parser

        return date_parser.parse(s).date().isoformat()
    except Exception:
        return value


def extract_id(item_data, id_spec, element=None):
    """
    Extract a unique ID from item data.

    id_spec: {
      "source": "url",          # which variable to use as source
      "regex": ",(\\d+)/$"      # optional regex to extract the ID
    }

    Or for reading an HTML attribute directly from the element:

    id_spec: {
      "type": "attribute",
      "name": "id"              # HTML attribute name on the matched element
    }
    """
    if not id_spec:
        # Fallback: use url if available, otherwise hash all values
        return item_data.get("url", str(hash(frozenset(item_data.items()))))

    # Read ID from an HTML attribute on the element itself
    if id_spec.get("type") == "attribute" and element is not None:
        return str(element.get(id_spec.get("name", "id"), ""))

    source_value = item_data.get(id_spec.get("source", ""), "")
    regex = id_spec.get("regex")
    if regex and source_value:
        match = re.search(regex, source_value)
        if match:
            return match.group(1) if match.lastindex else match.group(0)

    return source_value


def should_include(element, filter_spec):
    """
    Check if an element passes the filter.

    filter_spec: {
      "selector": ".job__header-details--date",
      "exclude_class": "job__header-details--closed"
    }
    """
    if not filter_spec:
        return True

    target = element.select_one(filter_spec["selector"])
    if target is None:
        return False

    exclude_class = filter_spec.get("exclude_class")
    if exclude_class:
        classes = target.get("class") or []
        if exclude_class in classes:
            return False

    return True


def apply_find(element, find_steps):
    """Apply a chain of traversal steps to an element, returning the result.

    Each step is [method_name, ...args]. Supported methods:
      - ["until", selector]  — collect next siblings until one contains selector
      - ["select", selector] — CSS select_one within current context
      - ["siblings"]         — collect all next siblings into a fragment
    """
    current = element
    for step in find_steps:
        if current is None:
            return None
        method = step[0]
        if method == "until":
            selector = step[1]
            collected = []
            for sib in current.next_siblings:
                if not hasattr(sib, "name") or not sib.name:
                    continue
                collected.append(sib)
                if sib.select_one(selector):
                    break
            if not collected:
                return None
            wrapper = BeautifulSoup("", "html.parser")
            container = wrapper.new_tag("div")
            for el in collected:
                container.append(copy.copy(el))
            current = container
        elif method == "select":
            selector = step[1]
            current = current.select_one(selector)
        elif method == "siblings":
            collected = []
            for sib in current.next_siblings:
                if not hasattr(sib, "name") or not sib.name:
                    continue
                collected.append(sib)
            if not collected:
                return None
            wrapper = BeautifulSoup("", "html.parser")
            container = wrapper.new_tag("div")
            for el in collected:
                container.append(copy.copy(el))
            current = container
    return current


def apply_transform(element, transform_steps):
    """Apply transformation steps to a DOM element (modifies a copy).

    Each step is [method_name, ...args]. Supported methods:
      - ["remove", selector]      — remove all elements matching the CSS selector
      - ["remove_after", selector] — remove first match and all following siblings
    """
    el = copy.copy(element)
    for step in transform_steps:
        method = step[0]
        if method == "remove":
            selector = step[1]
            for match in el.select(selector):
                match.decompose()
        elif method == "remove_after":
            selector = step[1]
            match = el.select_one(selector)
            if match:
                parent = match.parent
                remove = False
                for child in list(parent.children):
                    if child is match:
                        remove = True
                    if remove:
                        child.extract()
    return el


def extract_variables(element, variables_spec, locale=None):
    """Extract all defined variables from an element.

    Supports an optional "sibling" key in the variable spec. When present,
    the selector is applied to the next sibling element(s) instead of
    inside the matched element.

    Supports an optional "find" key as an alternative to "sibling" + "selector".
    "find" is an array of traversal steps chained like jQuery methods.
    When present, it replaces both "sibling" and "selector" for locating
    the extraction target.

    Supports an optional "transform" key for modifying the DOM fragment
    before value extraction (e.g. removing signature elements).

    Supports an optional "collect" key. When true, all elements matching
    the selector are collected and returned as a list.
    """
    data = {}
    for var_name, var_spec in variables_spec.items():
        # json+query variables need item ID, extracted separately in parse_items()
        value = var_spec.get("value", {})
        if value.get("parse") == "json" and value.get("query"):
            continue

        find_steps = var_spec.get("find")
        if find_steps:
            search_root = apply_find(element, find_steps)
            if search_root is None:
                data[var_name] = var_spec.get("default", "")
                continue
            transform = var_spec.get("transform")
            if transform:
                search_root = apply_transform(search_root, transform)
            value = extract_value(
                search_root, var_spec["value"],
                var_spec.get("default"), locale=locale
            )
            data[var_name] = value if value is not None else ""
            continue

        search_root = element
        if var_spec.get("sibling"):
            # Walk through next siblings until we find a non-spacer element
            sibling = element.find_next_sibling()
            while (
                sibling
                and sibling.get("class")
                and "spacer" in sibling.get("class", [])
            ):
                sibling = sibling.find_next_sibling()
            search_root = sibling

        if search_root is None:
            data[var_name] = var_spec.get("default", "")
            continue

        default = var_spec.get("default")
        selector = var_spec["selector"]

        if var_spec.get("collect"):
            # Collect all matching elements into a list
            sub_elements = search_root.select(selector)
            values = []
            for sub_el in sub_elements:
                val = extract_value(sub_el, var_spec["value"], locale=locale)
                if val is not None and val != "":
                    values.append(str(val))
            data[var_name] = values
        elif selector == ":self":
            # Special selector: use the container element itself
            value = extract_value(
                search_root, var_spec["value"], default, locale=locale
            )
            data[var_name] = value if value is not None else ""
        else:
            sub_element = search_root.select_one(var_spec["selector"])
            value = extract_value(
                sub_element, var_spec["value"], default, locale=locale
            )
            data[var_name] = value if value is not None else ""
    return data


def parse_items(html, query_spec, locale=None, bs_parser="html.parser"):
    """
    Parse items from HTML based on query specification.

    Returns a list of dicts with extracted variables + 'id' field.
    """
    _json_cache.clear()
    soup = BeautifulSoup(html, bs_parser)
    query_type = query_spec["type"]
    selector = query_spec["selector"]
    variables = query_spec.get("variables", {})
    filter_spec = query_spec.get("filter")
    id_spec = query_spec.get("id")

    # Identify json+query variables (need item ID for JMESPath Liquid rendering)
    json_query_vars = {
        name: spec
        for name, spec in variables.items()
        if spec.get("value", {}).get("parse") == "json"
        and spec.get("value", {}).get("query")
    }

    def extract_json_query_vars(data, element):
        """Extract json+query variables using cached parsed JSON."""
        for var_name, var_spec in json_query_vars.items():
            value_spec = var_spec["value"]
            query_spec_json = value_spec["query"]
            sel = var_spec["selector"]

            # Cache parsed JSON per selector to avoid re-parsing per item
            cache_key = (id(soup), sel)
            if cache_key not in _json_cache:
                json_el = soup.select_one(sel)
                raw = extract_value(json_el, value_spec, locale=locale)
                if not isinstance(raw, dict) and not isinstance(raw, list):
                    data[var_name] = [] if query_spec_json.get("type") == "list" else {}
                    continue
                _json_cache[cache_key] = raw
            json_data = _json_cache[cache_key]
            data[var_name] = query_json(json_data, query_spec_json, data)

    if query_type == "single":
        element = soup.select_one(selector)
        if element is None:
            return []
        if not should_include(element, filter_spec):
            return []
        data = extract_variables(element, variables, locale=locale)
        data["id"] = extract_id(data, id_spec, element)
        if json_query_vars:
            extract_json_query_vars(data, element)
        return [data]

    elif query_type == "list":
        elements = soup.select(selector)
        items = []
        for el in elements:
            if not should_include(el, filter_spec):
                continue
            data = extract_variables(el, variables, locale=locale)
            data["id"] = extract_id(data, id_spec, el)
            if json_query_vars:
                extract_json_query_vars(data, el)
            items.append(data)
        return items

    return []


def find_next_page_url(html, pagination_spec, current_url):
    """
    Find the next page URL based on pagination config.

    Supports two pagination types:

    "next_link" - follow a specific "next" link on the page:
        { "type": "next_link", "selector": "a.morelink", "base_url": "..." }

    "numbered" - find the next numbered page after the active one:
        { "type": "numbered", "selector": ".pagination .pagination__page",
          "active_class": "pagination__page--active", "base_url": "..." }
    """
    if not pagination_spec:
        return None

    soup = BeautifulSoup(html, "html.parser")
    base_url = pagination_spec.get("base_url", current_url)
    pag_type = pagination_spec.get("type", "next_link")

    if pag_type == "next_link":
        link = soup.select_one(pagination_spec["selector"])
        if link:
            href = str(link.get("href", ""))
            if href:
                return urljoin(base_url, href)
        return None

    elif pag_type == "numbered":
        all_pages = soup.select(pagination_spec["selector"])
        active_class = pagination_spec.get("active_class", "")
        found_active = False
        for page_link in all_pages:
            classes = page_link.get("class") or []
            if active_class and active_class in classes:
                found_active = True
                continue
            if found_active:
                href = str(page_link.get("href", ""))
                if href:
                    return urljoin(base_url, href)
                break
        return None

    return None


def dump_html(html, url):
    """Save raw HTML to a timestamped file for diagnostics."""
    dumps_dir = os.path.join(DATA_DIR, "dumps")
    os.makedirs(dumps_dir, exist_ok=True)
    safe_url = re.sub(r'[^\w\-.]', '_', url)[:100]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dump_file = os.path.join(dumps_dir, f"{timestamp}_{safe_url}.html")
    try:
        with open(dump_file, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"  HTML dump saved: {dump_file}")
    except Exception:
        pass


def check_expect(html, expect_selectors, url, bs_parser="html.parser"):
    """
    Verify that expected CSS selectors exist on the page.

    Returns a list of missing selector strings. An empty list means
    all expectations are met. Used to detect HTML structure changes
    that would silently break scraping.
    """
    if not expect_selectors:
        return []

    soup = BeautifulSoup(html, bs_parser)
    missing = []
    for selector in expect_selectors:
        if not soup.select_one(selector):
            missing.append(selector)

    return missing


def _render_http_options(definition, params, auth_data=None):
    """Render headers and body from definition with Liquid context."""
    ctx = liquid_context(params, auth_data)
    rendered_headers = _render_liquid_dict(definition.get("headers", {}), ctx)
    body = definition.get("body")
    rendered_body = _render_liquid_dict(body, ctx) if isinstance(body, dict) else None
    return rendered_headers, rendered_body


def fetch_all_items(definition, params, def_name=None):
    """
    Fetch all items across all pages for a definition.
    Returns list of item dicts.

    Supports format: "html" (default), "xml", or "json".
    When "query" is absent, performs a health check: returns a single item
    with HTTP response metadata (code, method, body, headers, error).
    Raises ValueError if 'expect' selectors are missing from the page.
    """
    fmt = definition.get("format", "html")
    query_spec = definition.get("query")
    user_agent = definition.get("userAgent")
    method = definition.get("method", "GET")

    # Resolve auth
    auth_headers, auth_cookies, auth_data = resolve_auth(definition, params, def_name)

    # Render HTTP options
    rendered_headers, rendered_body = _render_http_options(definition, params, auth_data)
    rendered_headers.update(auth_headers)

    # Build cookie header
    if auth_cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in auth_cookies.items())
        existing = rendered_headers.get("Cookie", "")
        rendered_headers["Cookie"] = f"{existing}; {cookie_str}" if existing else cookie_str

    url_template = definition.get("url", "")
    url = render_url(url_template, params, auth_data) if url_template else ""
    all_items = []

    if not query_spec:
        # Health check mode: return HTTP response metadata
        log(f"  Health check: {url}")
        item = {"id": url, "url": url}
        req_headers = {"User-Agent": user_agent or USER_AGENT}
        req_headers.update(rendered_headers)
        try:
            kwargs = {"headers": req_headers, "timeout": 30}
            if method.upper() == "POST" and rendered_body is not None:
                kwargs["json"] = rendered_body
            start = time.monotonic()
            resp = requests.request(method.upper(), url, **kwargs)
            elapsed = time.monotonic() - start
            item["http"] = {
                "code": resp.status_code,
                "method": method,
                "body": resp.text,
                "headers": {k.lower(): v for k, v in resp.headers.items()},
                "response_time": round(elapsed, 3),
                "error": None,
            }
        except Exception as e:
            item["http"] = {
                "code": 0,
                "method": method,
                "body": "",
                "headers": {},
                "response_time": 0.0,
                "error": str(e),
            }
        return [item]

    if fmt == "json":
        sources = definition.get("sources")
        if sources:
            # Multi-source: fetch multiple URLs, merge into one item
            item = {}
            ctx = liquid_context(params, auth_data)
            for source in sources:
                src_url = liquid.from_string(source["url"]).render(**ctx)
                src_method = source.get("method", method)
                src_headers = dict(rendered_headers)
                src_headers.update(_render_liquid_dict(source.get("headers", {}), ctx))
                log(f"  Fetching JSON ({source['name']}): {src_url}")
                try:
                    json_data = fetch_json(
                        src_url, method=src_method, headers=src_headers,
                        user_agent=user_agent,
                    )
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 401 and definition.get("auth"):
                        result = retry_auth(definition["auth"], params, def_name, auth_data)
                        if result:
                            src_headers.update(result[0])
                            json_data = fetch_json(
                                src_url, method=src_method, headers=src_headers,
                                user_agent=user_agent,
                            )
                        else:
                            raise
                    else:
                        raise
                # Extract variables from this source
                src_query = source.get("query", {})
                if src_query:
                    src_items = parse_json_items(json_data, src_query)
                    if src_query.get("type") == "list":
                        item[source["name"]] = src_items
                    elif src_items:
                        # Single: merge variables into item directly
                        for k, v in src_items[0].items():
                            if k != "id":
                                item[source["name"] + "_" + k] = v
                else:
                    item[source["name"]] = json_data
            # Set ID from params
            id_spec = definition.get("query", {}).get("id")
            if id_spec:
                item["id"] = extract_id(item, id_spec)
            all_items = [item]
        else:
            # Single URL JSON path
            log(f"  Fetching JSON: {url}")
            try:
                json_data = fetch_json(
                    url, method=method, headers=rendered_headers,
                    body=rendered_body, user_agent=user_agent,
                )
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 401 and definition.get("auth"):
                    result = retry_auth(definition["auth"], params, def_name, auth_data)
                    if result:
                        rendered_headers.update(result[0])
                        json_data = fetch_json(
                            url, method=method, headers=rendered_headers,
                            body=rendered_body, user_agent=user_agent,
                        )
                    else:
                        raise
                else:
                    raise
            all_items = parse_json_items(json_data, query_spec)

    else:
        # HTML/XML path
        pagination_spec = definition.get("pagination")
        max_pages = pagination_spec.get("max_pages", 1) if pagination_spec else 1
        expect_selectors = query_spec.get("expect")
        bs_parser = "xml" if fmt == "xml" else "html.parser"
        page_num = 1

        while page_num <= max_pages:
            log(f"  Fetching page {page_num}: {url}")
            try:
                html, locale = fetch_page(
                    url, user_agent=user_agent, is_xml=bs_parser == "xml",
                    method=method, headers=rendered_headers, body=rendered_body,
                )
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 401 and definition.get("auth"):
                    result = retry_auth(definition["auth"], params, def_name, auth_data)
                    if result:
                        rendered_headers.update(result[0])
                        html, locale = fetch_page(
                            url, user_agent=user_agent, is_xml=bs_parser == "xml",
                            method=method, headers=rendered_headers, body=rendered_body,
                        )
                    else:
                        raise
                else:
                    raise

            # Check expected structure on first page only
            if page_num == 1 and expect_selectors:
                missing = check_expect(html, expect_selectors, url, bs_parser=bs_parser)
                if missing:
                    dump_html(html, url)
                    raise ValueError(
                        f"HTML structure changed at {url}. "
                        f"Missing expected selector(s): {', '.join(missing)}"
                    )

            # Check reject selectors
            reject_selectors = query_spec.get("reject")
            if reject_selectors:
                soup = BeautifulSoup(html, bs_parser)
                for sel in reject_selectors:
                    if soup.select_one(sel):
                        log(f"  Reject selector matched: {sel} — skipping results")
                        return all_items

            items = parse_items(html, query_spec, locale=locale, bs_parser=bs_parser)
            if not items:
                break
            all_items.extend(items)

            next_url = find_next_page_url(html, pagination_spec, url)
            if next_url:
                url = next_url
                page_num += 1
            else:
                break

    return all_items


def load_template(template_path):
    """Load a Liquid template file."""
    # Template paths in config are relative to the config file directory
    if not os.path.isabs(template_path):
        template_path = os.path.join(MUTIMON_DIR, template_path)

    if not os.path.exists(template_path):
        print(f"Warning: Template not found at {template_path}", file=sys.stderr)
        return None

    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def render_email(template_str, subject_template, items, params, definition,
                 *, input_groups=None):
    """
    Render the email body and subject using Liquid templates.

    Context includes:
      - all params (e.g. query)
      - items: list of extracted data dicts (with index added),
               or list of lists when input_groups is provided
      - count: total number of items
      - now: current datetime string
      - search_url: the rendered URL (first input when grouped)
    """
    search_url = render_url(definition.get("url", ""), params)

    if input_groups:
        indexed_groups = []
        total_count = 0
        global_index = 1
        for group_info, group_items in zip(input_groups, items):
            indexed_group = []
            for item in group_items:
                item_copy = dict(item)
                item_copy["index"] = global_index
                item_copy["_search_url"] = group_info["search_url"]
                item_copy["_input"] = group_info["params"]
                indexed_group.append(item_copy)
                global_index += 1
            indexed_groups.append(indexed_group)
            total_count += len(group_items)
        context = liquid_context(params)
        context["items"] = indexed_groups
        context["count"] = total_count
    else:
        indexed_items = []
        for i, item in enumerate(items, 1):
            item_copy = dict(item)
            item_copy["index"] = i
            indexed_items.append(item_copy)
        context = liquid_context(params)
        context["items"] = indexed_items
        context["count"] = len(items)

    context["now"] = str(datetime.now())
    context["search_url"] = search_url

    body = liquid.from_string(template_str).render(**context)
    subject = (
        liquid.from_string(subject_template).render(**context)
        if subject_template
        else ""
    )

    return subject, body


def send_email(config, recipient, subject, body):
    """Send an email notification."""
    server_config = config["email"]["server"]
    sender = server_config["email"]

    msg = EmailMessage()
    msg["From"] = f"Mutimon <{sender}>"
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(server_config["host"], server_config["port"]) as server:
        server.starttls()
        server.login(sender, server_config["password"])
        server.send_message(msg)
    info(f"  Email sent to {recipient}")


def save_email_to_file(rule_name, subject, body):
    """Save email to a file for debugging/verification."""
    email_dir = os.path.join(DATA_DIR, "emails")
    os.makedirs(email_dir, exist_ok=True)
    email_file = os.path.join(email_dir, f"{rule_name}.txt")
    with open(email_file, "w", encoding="utf-8") as f:
        f.write(f"Subject: {subject}\n")
        f.write(f"Date: {datetime.now()}\n")
        f.write("=" * 60 + "\n\n")
        f.write(body)
    log(f"  Email saved to {email_file}")


def _resolve_var(item, var_path):
    """Resolve a dot-separated variable path against a nested item dict."""
    parts = var_path.split(".")
    value = item
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part, "")
        else:
            return ""
    return value


def evaluate_single_validator(validator, item):
    """
    Evaluate a single validator object against an item's variables.

    The validator is a dict with optional keys (AND logic — all must pass):

      "test": expression-py expression with Liquid variables, e.g.:
          "{{ price }} > 9.5"
          "title =~ /^Ask HN/"
          "skills & ['AI', 'ML']"

      "match": regex match against a rendered variable (deprecated for regex,
          use 'test' with =~ instead; include/exclude still supported), e.g.:
          {"var": "skills", "include": ["AI", "ML"]}

    Returns True if all specified conditions pass, False otherwise.
    """
    # Evaluate "test" condition (expression-py expression)
    test_expr = validator.get("test")
    if test_expr:
        try:
            rendered = liquid.from_string(test_expr).render(**item).strip()
            vars_in_expr = re.findall(r"\{\{\s*(\w+)\s*\}\}", test_expr)
            missing = [v for v in vars_in_expr if v not in item]
            if missing:
                raise UndefinedVariableError(
                    f"Undefined variable(s) in validator test: "
                    f"{', '.join(missing)}. "
                    f"Expression: '{test_expr}', "
                    f"available: {sorted(item.keys())}"
                )
            expr = Expression()
            expr.variables = dict(item)
            if not bool(expr.evaluate(rendered)):
                return False
        except UndefinedVariableError:
            raise
        except Exception as e:
            print(
                f"Warning: Validator test failed for '{test_expr}': {e}",
                file=sys.stderr,
            )
            return False

    # Evaluate "match" condition(s) (regex/include/exclude match — AND logic)
    match_spec = validator.get("match")
    if match_spec:
        # Allow single object or array of match objects
        match_list = match_spec if isinstance(match_spec, list) else [match_spec]
        for m in match_list:
            try:
                if "var" in m:
                    value = _resolve_var(item, m["var"])
                else:
                    value = liquid.from_string(m["value"]).render(**item)
                should_exist = m.get("exist", True)
                strict = m.get("strict", False)
                if "exclude" in m:
                    if isinstance(value, list):
                        matched = not any(s in value for s in m["exclude"])
                    elif strict:
                        matched = not any(s == value for s in m["exclude"])
                    else:
                        matched = not any(s in value for s in m["exclude"])
                elif "include" in m:
                    if isinstance(value, list):
                        matched = any(s in value for s in m["include"])
                    elif strict:
                        matched = any(s == value for s in m["include"])
                    else:
                        matched = any(s in value for s in m["include"])
                else:
                    if isinstance(value, list):
                        value = ", ".join(value)
                    matched = bool(re.search(m["regex"], value))
                if not should_exist:
                    matched = not matched
                if not matched:
                    return False
            except Exception as e:
                print(
                    f"Warning: Validator match failed for '{m}': {e}", file=sys.stderr
                )
                return False

    return True


def evaluate_validator(validator, item):
    """
    Evaluate a validator against an item's variables.

    Accepts:
      - None/empty: always passes
      - dict: a single validator (AND logic within)
      - list: multiple validators with two-tier logic:
          - Validators with "require": true must ALL pass (AND)
          - Remaining validators use OR logic (at least one must pass)
          - If only required validators exist, OR check is skipped
    """
    if not validator:
        return True

    if isinstance(validator, list):
        required = [v for v in validator if v.get("require")]
        optional = [v for v in validator if not v.get("require")]

        # All required validators must pass
        for v in required:
            if not evaluate_single_validator(v, item):
                return False

        # At least one optional validator must pass (if any exist)
        if optional:
            return any(evaluate_single_validator(v, item) for v in optional)

        return True

    return evaluate_single_validator(validator, item)


def evaluate_track(track, item):
    """
    Evaluate track states against an item (state machine).

    Returns a dict with:
      - _state: index of the first matching state (or None)
      - _state_name: the "name" field or "test" expression of the matching state
      - _value: rendered value expression (if track has "value")
      - _silent: whether the matching state is silent
    """
    result = {"_state": None, "_state_name": None, "_silent": False}

    # Render tracked value
    if "value" in track:
        try:
            rendered = liquid.from_string(track["value"]).render(**item)
            try:
                result["_value"] = float(rendered)
            except (ValueError, TypeError):
                result["_value"] = rendered
        except Exception:
            result["_value"] = None

    # Evaluate states top-down, first match wins
    for i, state in enumerate(track["states"]):
        try:
            rendered = liquid.from_string(state["test"]).render(**item)
            expr = Expression()
            expr.variables = dict(item)
            if bool(expr.evaluate(rendered)):
                result["_state"] = i
                result["_state_name"] = state.get("name", state["test"])
                result["_silent"] = state.get("silent", False)
                break
        except Exception as e:
            print(
                f"Warning: Track state evaluation failed for '{state['test']}': {e}",
                file=sys.stderr,
            )
            continue

    return result


def resolve_validator(validator, validators_defs):
    """
    Resolve @id references in a validator against defs.validators.

    A validator (or array element) with {"@id": "name"} is replaced
    by the corresponding entry from validators_defs.
    """
    if not validator:
        return validator
    if isinstance(validator, dict) and "@id" in validator:
        return validators_defs.get(validator["@id"])
    if isinstance(validator, list):
        resolved = []
        for v in validator:
            if isinstance(v, dict) and "@id" in v:
                ref = validators_defs.get(v["@id"])
                if ref is not None:
                    if isinstance(ref, list):
                        resolved.extend(ref)
                    else:
                        resolved.append(ref)
            else:
                resolved.append(v)
        return resolved
    return validator


def _replace_each_placeholders(template, var_name, value):
    """Replace {{var}} or {{var.key}} placeholders with value (string or dict)."""
    import re
    if isinstance(value, str):
        return template.replace("{{" + var_name + "}}", value)
    prefix = var_name + "."
    def replacer(match):
        key = match.group(1)
        if key == var_name:
            return str(value)
        if key.startswith(prefix):
            path = key[len(prefix):]
            obj = value
            for part in path.split("."):
                if isinstance(obj, dict) and part in obj:
                    obj = obj[part]
                else:
                    return match.group(0)
            return str(obj)
        return match.group(0)
    return re.sub(r"\{\{(\s*" + re.escape(var_name) + r"(?:\.[a-zA-Z0-9_.]*)?)\}\}", replacer, template)


def expand_input_each(input_spec):
    """Expand an input with 'each' into a list of input entries."""
    each = input_spec["each"]
    var_name = each["var"]
    values = each["values"]
    template_params = input_spec.get("params", {})
    validator = input_spec.get("validator")
    track = input_spec.get("track")
    entries = []
    for value in values:
        params = {k: _replace_each_placeholders(v, var_name, value)
                  for k, v in template_params.items()}
        entry = {"params": params}
        if validator is not None:
            entry["validator"] = validator
        if track is not None:
            entry["track"] = track
        entries.append(entry)
    return entries


def _merge_def_validator(def_validator, input_validator):
    """
    Merge a definition-level validator with an input-level validator.

    The def validator is treated as a required base filter (AND with input).
    Returns the merged validator or whichever one is present.
    """
    if not def_validator:
        return input_validator
    if not input_validator:
        return def_validator

    # Normalize both to lists
    if isinstance(def_validator, dict):
        def_list = [def_validator]
    else:
        def_list = list(def_validator)

    if isinstance(input_validator, dict):
        input_list = [input_validator]
    else:
        input_list = list(input_validator)

    # Mark all def validators as required (AND with input)
    merged = []
    for v in def_list:
        entry = dict(v)
        entry["require"] = True
        merged.append(entry)
    merged.extend(input_list)
    return merged


def resolve_inputs(rule, validators_defs=None, definition=None):
    """
    Resolve the input entries for a rule.

    Returns a list of {params, validator, track} dicts.

    When a definition is provided, its validator and track are merged:
      - def validator: AND-merged with input validator (as required base filter)
      - def track: used as default, overridden by input-level track
      - track and validator are mutually exclusive: input-level track
        suppresses def validator, input-level validator suppresses def track

    Supports:
      - No 'input' field: uses rule's 'params' directly, no validator/track
      - 'input' as a single object: wraps in a list
      - 'input' as an array: used as-is

    Each input entry can have:
      - 'params': dict of URL template variables
      - 'validator': filter expression (mutually exclusive with 'track')
      - 'track': state machine threshold tracking (mutually exclusive with 'validator')
    """
    if validators_defs is None:
        validators_defs = {}

    def_validator = None
    def_track = None
    if definition:
        def_validator = resolve_validator(
            definition.get("validator"), validators_defs
        )
        def_track = definition.get("track")

    input_spec = rule.get("input")
    if input_spec is None:
        params = rule.get("params", {})
        if isinstance(params, list):
            return [
                {
                    "params": p,
                    "validator": def_validator if not def_track else None,
                    "track": def_track if not def_validator else None,
                    "ref": None,
                    "label": None,
                }
                for p in params
            ]
        return [{
            "params": params,
            "validator": def_validator if not def_track else None,
            "track": def_track if not def_validator else None,
            "ref": None,
            "label": None,
        }]
    if isinstance(input_spec, dict):
        if "each" in input_spec:
            input_spec = expand_input_each(input_spec)
        else:
            input_spec = [input_spec]
    results = []
    for entry in input_spec:
        input_validator = resolve_validator(entry.get("validator"), validators_defs)
        input_track = entry.get("track")
        # Track and validator are mutually exclusive:
        # input track suppresses def validator, input validator suppresses def track
        if input_track:
            track = input_track
            validator = None
        elif input_validator:
            track = None
            validator = _merge_def_validator(def_validator, input_validator)
        else:
            # No input-level override — use def-level
            track = def_track
            validator = def_validator if not def_track else None
        params = entry.get("params")
        if params is None:
            params = rule.get("params", {})
        entry_ref = entry.get("ref")
        entry_label = entry.get("label")
        if isinstance(params, list):
            for p in params:
                results.append({
                    "params": p, "validator": validator, "track": track,
                    "ref": entry_ref, "label": entry_label,
                })
        else:
            results.append({
                "params": params, "validator": validator, "track": track,
                "ref": entry_ref, "label": entry_label,
            })
    return results


def process_rule(config, rule, save_only=False, init=False):
    """Process a single rule: fetch, diff, notify."""
    rule_name = rule["name"]
    default_ref = rule.get("ref")
    recipient = rule.get("email", config["email"]["server"]["email"])
    template_path = rule.get("template", "")
    subject_template = rule.get("subject", "")
    logging = rule.get("log", False)

    log(f"Processing rule: '{rule_name}' (ref: {default_ref or '<per-input>'})")

    # Resolve inputs (multiple pages with different params + validators)
    validators_defs = config.get("defs", {}).get("validators", {})
    default_def = config["defs"].get(default_ref) if default_ref else None
    inputs = resolve_inputs(rule, validators_defs, definition=default_def)

    # Aggregated rule = any input has its own ref. Triggers ID namespacing.
    aggregated = any(inp.get("ref") for inp in inputs)
    if default_ref is None and not aggregated:
        print(
            f"Error: Rule '{rule_name}' has no 'ref' and no input with 'ref'",
            file=sys.stderr,
        )
        return

    all_items = []
    input_groups = []
    flatten = rule.get("flatten", True)

    has_track = any(inp.get("track") for inp in inputs)

    for input_index, input_entry in enumerate(inputs):
        params = input_entry["params"]
        validator = input_entry["validator"]
        track = input_entry.get("track")
        entry_ref = input_entry.get("ref") or default_ref
        definition = config["defs"].get(entry_ref)
        if not definition:
            print(
                f"Error: Definition '{entry_ref}' not found in config.defs",
                file=sys.stderr,
            )
            continue
        label = input_entry.get("label") or entry_ref

        try:
            items = fetch_all_items(definition, params, def_name=entry_ref)
        except ValueError as e:
            # Structure change detected — send error email
            msg = str(e)
            print(f"Warning: {msg}", file=sys.stderr)
            send_error_email(
                f"[mutimon] HTML structure changed for '{rule_name}'",
                f"Rule '{rule_name}' (ref: {entry_ref}) "
                f"detected a page structure change.\n\n{msg}",
            )
            continue
        except Exception as e:
            print(f"Error: fetching data for params {params}: {e}", file=sys.stderr)
            continue

        # Merge params into each item and re-derive ID
        id_spec = definition.get("query", {}).get("id")
        for item in items:
            for k, v in params.items():
                if k not in item:
                    item[k] = v
            if id_spec and not item.get("id"):
                item["id"] = extract_id(item, id_spec)
            # Namespace IDs in aggregated rules to prevent cross-source collisions
            if aggregated and item.get("id"):
                item["id"] = f"{entry_ref}:{item['id']}"
            item["_label"] = label

        # Mark each item with validator result or track state
        if track:
            for item in items:
                result = evaluate_track(track, item)
                item["_state"] = result["_state"]
                item["_state_name"] = result["_state_name"]
                item["_silent"] = result["_silent"]
                if "_value" in result:
                    item["_value"] = result["_value"]
                item["_valid"] = True  # track keeps all items
        else:
            try:
                for item in items:
                    item["_valid"] = (
                        evaluate_validator(validator, item) if validator else True
                    )
            except UndefinedVariableError as e:
                msg = str(e)
                print(f"Error: {msg}", file=sys.stderr)
                send_error_email(
                    f"[mutimon] Config error in rule '{rule_name}'",
                    f"Rule '{rule_name}' (ref: {entry_ref}) has an invalid validator.\n\n{msg}",
                )
                return
            valid_count = sum(1 for i in items if i["_valid"])
            if validator and valid_count != len(items):
                log(f"  Validator: {valid_count}/{len(items)} item(s) passed")

        search_url = render_url(definition.get("url", ""), params)
        input_groups.append({
            "params": dict(params),
            "search_url": search_url,
        })
        if not flatten:
            for item in items:
                item["_group_index"] = input_index
        all_items.extend(items)

    # Deduplicate items by ID when flattening (multiple inputs may overlap)
    if flatten and len(inputs) > 1:
        seen_ids = set()
        unique_items = []
        for item in all_items:
            item_id = item.get("id")
            if item_id is not None and item_id in seen_ids:
                continue
            if item_id is not None:
                seen_ids.add(item_id)
            unique_items.append(item)
        if len(unique_items) < len(all_items):
            log(f"  Deduplicated: {len(all_items)} → {len(unique_items)} item(s)")
        all_items = unique_items

    dedupe_fields = rule.get("dedupe")
    if dedupe_fields:
        seen_keys = set()
        unique_items = []
        for item in all_items:
            key = tuple(str(item.get(f, "")) for f in dedupe_fields)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_items.append(item)
        if len(unique_items) < len(all_items):
            log(f"  Deduplicated by {dedupe_fields}: "
                f"{len(all_items)} → {len(unique_items)} item(s)")
        all_items = unique_items

    log(f"  Found {len(all_items)} item(s) total.")

    if not all_items:
        log("  No items found. Nothing to do.")
        save_last_run(rule_name)
        return

    # Load previous state and build lookup by ID
    known_items = load_state(rule_name)
    known_by_id = {}
    for item in known_items:
        if "id" in item:
            known_by_id[item["id"]] = item

    known_ids = set(known_by_id.keys())
    returning_ids = set()
    if logging:
        current_ids = {item.get("id") for item in all_items if item.get("id")}
        new_ids = current_ids - known_ids
        disappeared_ids = known_ids - current_ids
        rule_log(rule_name, f"--- RUN START (fetched={len(all_items)}, "
                 f"known={len(known_items)}) ---")
        rule_log(rule_name, f"  Current IDs: {sorted(current_ids)}")
        if new_ids:
            rule_log(rule_name, f"  New IDs (not in state): {sorted(new_ids)}")
        if disappeared_ids:
            rule_log(rule_name, f"  Disappeared IDs (in state, not on page): "
                     f"{sorted(disappeared_ids)}")

    # Find items to notify about
    notify_always = rule.get("notify") == "always"
    notify_items = []
    if notify_always:
        notify_items = [item for item in all_items if item.get("_valid", True)]
    elif has_track:
        # Track mode: notify on state transitions (unless silent)
        for item in all_items:
            item_id = item.get("id")
            current_state = item.get("_state")
            prev = known_by_id.get(item_id)
            prev_state = prev.get("_state") if prev else None

            # Attach previous state info for templates
            item["_prev_state"] = prev_state
            item["_prev_state_name"] = prev.get("_state_name") if prev else None

            if current_state is None:
                continue
            if prev is None:
                if not item.get("_silent", False):
                    notify_items.append(item)
                    if logging:
                        rule_log(rule_name, f"  NOTIFY (new+track): "
                                 f"id={item_id} state={current_state}")
            elif current_state != prev_state:
                if not item.get("_silent", False):
                    notify_items.append(item)
                    if logging:
                        rule_log(rule_name, f"  NOTIFY (state change): "
                                 f"id={item_id} {prev_state} -> {current_state}")
    else:
        # Validator mode: notify on _valid transitions
        for item in all_items:
            item_id = item.get("id")
            if not item["_valid"]:
                continue
            prev = known_by_id.get(item_id)
            if prev is None:
                notify_items.append(item)
                if logging:
                    reason = "new"
                    if item_id in known_ids:
                        reason = "RETURNING (was in state, disappeared, back now)"
                        returning_ids.add(item_id)
                    rule_log(rule_name, f"  NOTIFY ({reason}): id={item_id} "
                             f"title={item.get('title', '')[:60]}")
            elif not prev.get("_valid", True):
                notify_items.append(item)
                if logging:
                    rule_log(rule_name, f"  NOTIFY (threshold crossed): "
                             f"id={item_id} title={item.get('title', '')[:60]}")

    if logging:
        rule_log(rule_name, f"  Total notifications: {len(notify_items)}")
        if returning_ids:
            rule_log(rule_name, f"  !! RETURNING IDs (likely cause of duplicates): "
                     f"{sorted(returning_ids)}")

    if notify_items:
        if init:
            info(f"[{rule_name}] {len(notify_items)} new item(s) — skipping notification (--init)")
        else:
            info(f"[{rule_name}] {len(notify_items)} new item(s) — sending notification")

            # Use first input's params as the base template context
            base_params = inputs[0]["params"]

            # Load and render template
            template_str = load_template(template_path)
            if template_str:
                if not flatten and len(input_groups) > 1:
                    grouped = [[] for _ in input_groups]
                    for item in notify_items:
                        grouped[item.get("_group_index", 0)].append(item)
                    filtered = [
                        (input_groups[i], g)
                        for i, g in enumerate(grouped) if g
                    ]
                    grouped_info = [pair[0] for pair in filtered]
                    grouped_items = [pair[1] for pair in filtered]
                    subject, body = render_email(
                        template_str, subject_template, grouped_items,
                        base_params, definition, input_groups=grouped_info
                    )
                else:
                    subject, body = render_email(
                        template_str, subject_template, notify_items,
                        base_params, definition
                    )
                if save_only:
                    save_email_to_file(rule_name, subject, body)
                else:
                    try:
                        send_email(config, recipient, subject, body)
                    except Exception as e:
                        print(
                            f"Error: Failed to send email: {e}", file=sys.stderr
                        )
                        print(
                            "State not saved — will retry on next run.",
                            file=sys.stderr,
                        )
                        return
                    save_email_to_file(rule_name, subject, body)
            else:
                print("Warning: No template found, skipping email.", file=sys.stderr)
    else:
        log(f"[{rule_name}] No changes to notify about.")

    # Strip transient fields before saving
    now_iso = datetime.now().isoformat()
    for item in all_items:
        item.pop("_prev_state", None)
        item.pop("_prev_state_name", None)
        item.pop("_silent", None)
        item["_last_seen"] = now_iso

    # Merge with previous state: keep disappeared items for retention period
    retain_days = 30
    current_ids = {item.get("id") for item in all_items if item.get("id")}
    now_ts = datetime.now().timestamp()
    cutoff = now_ts - retain_days * 86400
    retained = 0
    pruned = 0
    for item in known_items:
        item_id = item.get("id")
        if item_id and item_id not in current_ids:
            last_seen = item.get("_last_seen")
            if last_seen:
                try:
                    ts = datetime.fromisoformat(last_seen).timestamp()
                except ValueError:
                    ts = now_ts
            else:
                ts = now_ts
            if ts > cutoff:
                all_items.append(item)
                retained += 1
            else:
                pruned += 1
    if retained or pruned:
        log(f"  State merge: {retained} retained, {pruned} pruned "
            f"(retain={retain_days}d)")
    if logging:
        if retained or pruned:
            rule_log(rule_name, f"  State merge: {retained} retained, "
                     f"{pruned} pruned (retain={retain_days}d)")
        rule_log(rule_name, "--- RUN END ---\n")

    for item in all_items:
        item.pop("_group_index", None)
    save_state(rule_name, all_items)
    save_last_run(rule_name)
    log(f"  State saved for '{rule_name}'")


class RuleNameCompleter:
    """argcomplete completer that returns rule names from config."""

    def __call__(self, **kwargs):
        try:
            config_path = os.path.join(MUTIMON_DIR, "config.json")
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            return [rule["name"] for rule in config.get("rules", [])]
        except Exception:
            return []


def build_parser():
    """Build the argparse parser (used by argcomplete for completion)."""
    guide_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "AI_GUIDE.md"
    )
    parser = argparse.ArgumentParser(
        description="Mutimon — config-driven web scraper and email notifier.",
        epilog="To add websites using AI:\n"
        "  claude -p \"$(mon --ai-guide) Add https://example.com to mon\"",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse data but don't send emails or update state",
    )
    parser.add_argument(
        "--save-email",
        action="store_true",
        help="Save email to file instead of sending",
    )
    rule_completer = RuleNameCompleter()
    parser.add_argument(
        "--force",
        nargs="*",
        default=None,
        metavar="RULE",
        help="Ignore schedules. Without arguments: run all rules. "
        "With rule names: run only those rules.",
    ).completer = rule_completer
    parser.add_argument(
        "--init",
        nargs="*",
        default=None,
        metavar="RULE",
        help="Seed state without sending notifications. "
        "Without arguments: init all rules. "
        "With rule names: init only those rules.",
    ).completer = rule_completer
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the config file against the schema and exit",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed progress output",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress all output",
    )
    parser.add_argument(
        "--ai-guide",
        action="store_true",
        help="Print the AI instruction guide for adding websites",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all rule names (usable with --force <rule>)",
    )
    parser.add_argument(
        "--cron",
        nargs="?",
        const="*/5 * * * *",
        default=None,
        metavar="SCHEDULE",
        help="Print a cron entry with the resolved path to mon. "
        "Optional schedule argument (default: '*/5 * * * *').",
    )
    parser.add_argument(
        "--completion",
        choices=["bash", "zsh", "fish"],
        metavar="SHELL",
        help="Output shell completion script (bash, zsh, or fish)",
    )
    return parser, guide_path


def run():
    parser, guide_path = build_parser()
    args = parser.parse_args()

    if args.completion:
        import argcomplete
        print(argcomplete.shellcode(["mon"], shell=args.completion))
        return

    if args.ai_guide:
        with open(guide_path, "r", encoding="utf-8") as f:
            print(f.read())
        return

    if args.cron is not None:
        mon_path = shutil.which("mon")
        if mon_path:
            mon_path = os.path.realpath(mon_path)
        else:
            mon_path = os.path.realpath(sys.argv[0])
        log_path = os.path.join(MUTIMON_DIR, "mutimon.log")
        print(f"{args.cron} {mon_path} -q >> {log_path} 2>&1")
        return

    global verbose
    verbose = args.verbose

    if args.quiet:
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")

    init_config()
    if is_skeleton_config():
        print_setup_guide()
    config = load_config()

    # Load secrets and merge email password
    global _secrets
    _secrets = load_secrets()
    if _secrets.get("email", {}).get("password"):
        config.setdefault("email", {}).setdefault("server", {})["password"] = (
            _secrets["email"]["password"]
        )

    if args.list:
        rules = config.get("rules", [])
        if not rules:
            print("No rules defined.")
        else:
            for rule in rules:
                print(rule["name"])
        return

    # Check if SMTP credentials are still the skeleton defaults
    skeleton_server = _load_skeleton_email_server()
    user_server = config.get("email", {}).get("server", {})
    if skeleton_server and _hash_dict(user_server) == _hash_dict(skeleton_server):
        print(
            "Error: SMTP credentials in config are still the default placeholder values.",
            file=sys.stderr,
        )
        print(
            f"Edit the \"email.server\" section in {CONFIG_FILE} with your real SMTP settings.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.validate:
        validate_config(config, validate_only=True)
        setup_liquid(config)
        print(f"Config at {CONFIG_FILE} is valid.")
        return

    validate_config(config)
    setup_liquid(config)

    rules = config.get("rules", [])
    if not rules:
        log("No rules to process.")
        return

    force_all = args.force is not None and len(args.force) == 0
    force_rules = set(args.force) if args.force else set()
    init_all = args.init is not None and len(args.init) == 0
    init_rules = set(args.init) if args.init else set()

    targeted_rules = force_rules | init_rules
    if targeted_rules:
        rule_names = {r["name"] for r in rules}
        unknown = targeted_rules - rule_names
        if unknown:
            print(
                f"Error: Rule(s) not found: {', '.join(sorted(unknown))}. "
                f"Available rules: {', '.join(sorted(rule_names))}",
                file=sys.stderr,
            )
            sys.exit(1)

    log(f"Processing {len(rules)} rule(s)...")

    for rule in rules:
        rule_name = rule["name"]

        if rule.get("enabled") is False:
            log(f"Skipping '{rule_name}' (disabled)")
            continue

        if targeted_rules and rule_name not in targeted_rules:
            continue

        skip_schedule = force_all or force_rules or init_all or init_rules
        if not skip_schedule and not args.dry_run and not should_run_now(rule):
            schedule = rule.get("schedule", "")
            log(f"Skipping '{rule_name}' (schedule: {schedule})")
            continue

        if args.dry_run:
            default_ref = rule.get("ref")
            default_def = config["defs"].get(default_ref) if default_ref else None
            info(f"[DRY RUN] Rule: '{rule_name}'")
            inputs = resolve_inputs(rule, definition=default_def)
            all_items = []
            for input_entry in inputs:
                params = input_entry["params"]
                validator = input_entry["validator"]
                entry_ref = input_entry.get("ref") or default_ref
                definition = config["defs"].get(entry_ref)
                if not definition:
                    print(f"  Error: Definition '{entry_ref}' not found", file=sys.stderr)
                    continue
                try:
                    items = fetch_all_items(definition, params, def_name=entry_ref)
                    if validator:
                        items = [i for i in items if evaluate_validator(validator, i)]
                    id_spec = definition.get("query", {}).get("id")
                    for item in items:
                        for k, v in params.items():
                            if k not in item:
                                item[k] = v
                        if id_spec and not item.get("id"):
                            item["id"] = extract_id(item, id_spec)
                    all_items.extend(items)
                except UndefinedVariableError as e:
                    print(f"  Error: {e}", file=sys.stderr)
                    break
                except Exception as e:
                    print(f"  Error for params {params}: {e}", file=sys.stderr)
            info(f"  Found {len(all_items)} item(s)")
            for item in all_items[:3]:
                log(f"    id={item.get('id', '?')}: {item}")
            if len(all_items) > 3:
                log(f"    ... and {len(all_items) - 3} more")
        else:
            is_init = init_all or rule_name in init_rules
            process_rule(config, rule, save_only=args.save_email, init=is_init)

    log("Done.")


def main():
    """Entry point for the mon command."""
    if "_ARGCOMPLETE" in os.environ:  # pragma: no cover - shell completion shim
        parser, _ = build_parser()
        import argcomplete
        argcomplete.autocomplete(parser)
        return
    try:
        run()
    except Exception:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        send_error_email(
            "[mutimon] Fatal error",
            f"Mutimon crashed at {datetime.now()}.\n\n{tb}",
        )


if __name__ == "__main__":  # pragma: no cover
    main()
