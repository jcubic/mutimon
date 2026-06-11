# Mutimon — Agent Context

## IMPORTANT: Data-Driven Architecture

**Never add hardcoded logic to the code.** This project is entirely data-driven — all scraping logic, filtering, and behavior must be configured through `config.json`, not by modifying `main.py`. The code must remain generic. If a new feature or filter is needed, implement it as a generic config option that any rule can use, not as a special case in the code.

## IMPORTANT: Secrets File

**NEVER read `~/.mutimon/secrets.json` directly.** This file contains passwords and API keys. Do not use the Read tool on it or include its contents in conversation context. If you need secret values at runtime (e.g. to test an API), use inline Python that loads and uses them without printing:

```python
python3 -c "
from mutimon.main import load_secrets
secrets = load_secrets()
# use secrets['umami']['username'] etc. in your code
"
```

## IMPORTANT: Lint Check

**Always run `ruff check src/` after editing Python files.** Fix any errors before committing. Use `ruff check src/ --fix` for auto-fixable issues.

## What this is

A generic, config-driven web scraper that monitors websites for changes and sends email notifications. Config lives at `~/.mutimon/config.json`, templates at `~/.mutimon/templates/`, state at `~/.mutimon/data/`. The command (`mon`) is installed via pip and run periodically via cron.

## Key files

- `src/mutimon/main.py` — main script, all logic in one file
- `src/mutimon/config.schema.json` — JSON Schema for validation, used by `--validate` and on every run
- `src/mutimon/skeleton/` — default config + templates copied to `~/.mutimon/` on first run
- `pyproject.toml` — pip package configuration, installs `mon` command
- `requirements.txt` — pinned minimum versions for all dependencies

## Architecture

1. `defs` — reusable scraping definitions (URL + CSS selectors + pagination)
   - `format` — `"html"` (default) or `"xml"` for RSS/Atom feeds and XML documents (uses lxml XML parser)
   - `userAgent` — optional custom User-Agent header for HTTP requests
   - `defs.commands` — reusable Liquid tag commands (e.g. `{% fresh date 604800 %}`, `{% today date %}`), usable in validator `test`/`match` expressions
   - `defs.filters` — custom Liquid filters defined as Liquid filter expression strings (e.g. `"clean": "replace_regex: '\\s+', ' ' | strip"`), usable as `{{ value | name }}` in templates. Built-in filters: `replace_regex` for regex substitution, `html2text` for converting HTML to plain text (preserves code blocks).
2. `rules` — reference a def, add schedule (cron expr), email template, recipient
3. `input` — optional array on a rule for scraping multiple pages with different params (e.g. multiple stock symbols)
4. `validator` — optional filter on each input entry, object or array:
   - `{"test": "{{price}} > 10"}` — numexpr expression
   - `{"match": {"var": "title", "regex": "^Ask HN"}}` — regex match
   - `{"match": {"var": "skills", "include": ["Linux"]}}` — include items where list contains any listed string (exact element match)
   - `{"match": {"var": "skills", "exclude": ["Angular", "C#"]}}` — exclude items where list contains any listed string
   - `var` — direct variable lookup (preserves lists); `value` — Liquid template (always string); use one or the other
   - `{"@id": "name"}` — reference to a reusable validator defined in `defs.validators`
5. `defs.validators` — reusable validators referenced by `{"@id": "name"}` in rules, avoids duplication across rules
   - Array of validators = OR logic (any passes)
   - `require: true` on a validator makes it mandatory (AND with others); remaining validators still OR-combine
   - Object with both test+match = AND logic
   - `match.exist: false` passes when pattern does NOT match (useful for detecting something disappearing from a page)
6. Threshold crossing — all fetched items are saved with a `_valid` flag. When an item passes a validator after previously failing (or vice versa), it triggers a re-notification. Enables alerts like "price went back above $75k after dipping".
7. `track` — state-machine threshold tracking (alternative to `validator`, mutually exclusive). Evaluates states top-down (first match wins), saves state index, notifies on every state transition. States can be `"silent": true` to save state without notifying. Template vars: `{{ item._state_name }}`, `{{ item._prev_state_name }}`, `{{ item._value }}`.
8. `parse: "json"` + `query` (JMESPath) — extracts structured data from embedded JSON. Variables are processed AFTER HTML extraction and ID assignment (so `{{id}}` is available in `query.path`). Parsed JSON is cached per page via module-level `_json_cache` keyed by `(id(soup), selector)`, cleared at start of each `parse_items()` call.
9. Health checks — definitions without `query` act as health checks. Returns a single item with `http.code`, `http.method`, `http.body`, `http.headers` (dict, lowercase keys), `http.response_time`, `http.error`. Connection errors produce `code: 0`. Combine with `track` for up/down notifications. The `var` field in `match` validators supports dot-separated paths for nested access (e.g. `http.headers.content-type`).
10. Def-level `validator` and `track` — definitions can include `validator` (AND-merged with input-level validators via `_merge_def_validator()`, def validators become `require: True`) and `track` (used as default, overridden by input-level track). Track/validator mutual exclusivity applies: input track suppresses def validator, input validator suppresses def track. Resolved in `resolve_inputs()`.
11. `dedupe` — optional array of variable names on a rule. Items with identical values for all listed fields are collapsed (first kept). Runs after ID-based dedup, before state comparison. Useful when the same listing appears with different IDs (e.g. job offers tagged under multiple categories).

## How to add a new page to scrape

1. Inspect the target page HTML (use browser DevTools or `python3 -c "import requests; ..."`)
2. Add a definition to `defs` in `~/.mutimon/config.json`:
   - `url` — supports `{{param}}` Liquid variables
   - `format` — `"html"` (default) or `"xml"` for RSS/Atom feeds and XML documents
   - `userAgent` — optional custom User-Agent string (some feeds block default agents)
   - `query.type` — `"list"` (multiple items) or `"single"` (one item per page)
   - `query.selector` — CSS selector for item container (for XML: element names like `item`, `entry`)
   - `query.variables` — each variable: `selector` + `value` (`type: "text"`, `type: "attribute"` with `name`, or `type: "html"` for raw inner HTML)
   - Optional: `pagination`, `filter`, `id`, `value.parse: "number"|"money"|"list"|"json"`, `value.regex`, `value.prefix`
   - Optional: `query.reject` — array of CSS selectors; if any match, the page returns 0 items (e.g. a "no results" indicator that hides recommended/unrelated content)
   - Optional: `sibling: true` on a variable to search next sibling element
   - Optional: `collect: true` on a variable to extract ALL matching elements as a list (use `{% for %}` in templates)
   - Optional: `selector: ":self"` to reference the container element itself (e.g. when container is `<a>` and you need its `href`)
   - Optional: `value.parse: "json"` + `value.query` with JMESPath for extracting structured data from embedded JSON (Next.js `__NEXT_DATA__`, JSON in attributes, etc.). `value.query.path` supports Liquid variables like `{{id}}` rendered per item. See README "JSON extraction" section.
   - Optional: `find` — array of chainable DOM traversal steps `[["select", sel], ["until", sel], ["siblings"]]` for navigating complex page structures where data isn't inside the container. Replaces `sibling` + `selector` when present.
   - Optional: `transform` — array of DOM mutation steps `[["remove", sel], ["remove_after", sel]]` applied to a copy of the element before value extraction (e.g. stripping signatures, UI buttons).
3. Add a rule to `rules`:
   - `ref` — definition name
   - `name` — unique, used as state filename
   - `schedule` — cron expression (e.g. `"0 8 * * *"`)
   - `subject` — Liquid template for email subject
   - `template` — path to body template relative to `~/.mutimon/`
   - `email` — recipient
   - `params` or `input` — parameter values (input supports array + validators)
4. Create a Liquid template file. Available vars: `{{ count }}`, `{{ now }}`, `{{ search_url }}`, `{% for item in items %}...{% endfor %}` with `{{ item.index }}` and all extracted variables
5. Validate: `mon --validate`

## CLI flags

```
--validate        validate config and exit
--dry-run         fetch and display data, no emails, no state changes
--save-email      save emails to file instead of sending
--force           ignore schedules, run all rules
--force <rule>    ignore schedule, run only the named rule (errors if name not found)
-v, --verbose     show detailed progress output
-q, --quiet       suppress all output including errors
```

## Validation

`validate_config()` runs in this order, each step exits on failure:

1. JSON Schema validation (`config.schema.json`) via `jsonschema.Draft202012Validator`
2. Cron syntax for each rule's `schedule` (string or array) via `croniter.is_valid()`
3. CSS selector syntax for `query.selector`, `query.expect[]`, `query.filter.selector`, `pagination.selector`, and every variable's `selector` via `soup.select()` (catches `SelectorSyntaxError`). `:self` is skipped.
4. JMESPath syntax for `value.query.path` and `value.query.variables.*.path` via `jmespath.compile()`. Liquid placeholders `{{...}}` are replaced with `0` before checking so they don't fail parsing.

## Dependencies

Python 3.12+ with: `requests`, `beautifulsoup4`, `lxml`, `python-liquid`, `croniter`, `numexpr`, `jsonschema`, `babel`, `jmespath`

Install via `pip install .` or `pip install -e .` for development.

## Error handling

- Missing dependency at import → error email sent, exits
- Invalid config → error email with validation errors, exits
- Runtime crash in `run()` → error email with traceback
- `send_error_email()` uses only stdlib (no third-party deps) so it works even when the crash is caused by a missing library
