<h1 align="center">
  <img src="https://github.com/jcubic/mutimon/blob/master/.github/logo.svg?raw=true"
       alt="A minimalist dark gray logo showing a sequence of icons connected by arrows: a notification bell on the left, pointing right to a calendar with a clock on top, which then points right to a globe/network icon. Below the icons is the word 'MUTIMON' in bold uppercase letters." />
</h1>

[![pip](https://img.shields.io/badge/pip-0.3.0-blue.svg)](https://pypi.org/project/mutimon/)
[![CI](https://github.com/jcubic/mutimon/actions/workflows/ci.yml/badge.svg)](https://github.com/jcubic/mutimon/actions/workflows/ci.yml)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/mutimon?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/mutimon)
[![mutimon GitHub repo](https://img.shields.io/badge/github-mutimon-orange?logo=github)](https://github.com/jcubic/mutimon)
[![Coverage Status](https://coveralls.io/repos/github/jcubic/mutimon/badge.svg?branch=master)](https://coveralls.io/github/jcubic/mutimon?branch=master)
[![LICENSE GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](https://github.com/jcubic/mutimon/blob/master/LICENSE)

A generic, config-driven web scraper that monitors websites for changes and sends email
notifications. Define what to scrape using CSS selectors in a JSON config file, and format
notifications with Liquid templates.

Designed to run as a cron job. Each rule has its own schedule (cron expression), so the script can
be invoked frequently (e.g. every 5 minutes) and each rule runs only when its schedule is due.

Configuration works best with AI agents like OpenClaw, ClaudeCode, OpenCode, or Codex.

## Installation

### From PyPI

```bash
pip install mutimon
```

This installs the `mon` command.

### From source

```bash
git clone https://github.com/jcubic/mutimon.git
cd mutimon
pip install .
```

This installs the `mon` command from the local source, including all dependencies.


### First run

On the first run, the tool creates `~/.mutimon/` with a skeleton config and example rules (Hacker News + Bitcoin price alerts):

```bash
mon
# Config not found at /home/user/.mutimon/config.json
# Creating skeleton configuration in /home/user/.mutimon...
# Done. Edit /home/user/.mutimon/config.json to configure your scraping rules.
```

Edit `~/.mutimon/config.json` with your SMTP credentials and scraping rules, then run again.

## Usage

```bash
mon                    # process rules; only prints notifications and errors
mon --force            # ignore schedules, run all rules now
mon --force <rule>     # ignore schedule, run only the named rule
mon --dry-run          # fetch and display data, bypass schedules, no state changes
mon --save-email       # save email to file instead of sending via SMTP
mon --validate         # validate config against schema and exit
mon --list             # list all rule names (usable with --force <rule>)
mon --ai-guide         # print the path to the AI instruction file for adding websites
mon --cron             # print a cron entry with resolved path (default: every 5 min)
mon --cron "0 8 * * *" # print a cron entry with a custom schedule
mon -v, --verbose      # show detailed progress (page fetches, counts, skipped rules)
mon -q, --quiet        # suppress all output including errors
```

### Cron setup

Use `--cron` to generate a cron entry with the correct resolved path (works with pyenv, virtualenvs, etc.):

```bash
mon --cron                  # default: every 5 minutes
mon --cron "0 * * * *"      # custom: every hour
```

Install it directly:

```bash
(crontab -l 2>/dev/null; mon --cron) | crontab -
```

Each rule's `schedule` field controls when it actually executes, so running `mon` frequently (e.g. every 5 minutes) is safe — rules only fire when their cron expression matches.

## File structure

```
~/.mutimon/
  config.json              # main configuration
  templates/               # Liquid email templates
    hackernews
  data/                    # state files (tracked items per rule)
    hackernews
    .lastrun_hackernews    # last run timestamp for schedule tracking
    emails/                # saved copies of sent emails
```

## Configuration

A [JSON Schema](config.schema.json) is provided for editor autocompletion and validation. Add `"$schema": "./config.schema.json"` to your config file, or point to the raw URL if hosted on GitHub.

The config is validated against the schema on every run. If the config is invalid, an error email with all validation errors is sent to all rule recipients and the script exits.

The config file (`~/.mutimon/config.json`) has three sections:

### `email` -- SMTP server

```json
"email": {
  "server": {
    "host": "smtp.example.com",
    "port": 587,
    "password": "your-password",
    "email": "you@example.com"
  }
}
```

### `defs` -- Reusable scraping definitions and commands

Each definition describes how to fetch and parse data from a website. The optional `commands` key defines reusable Liquid tag commands (see [Commands](#commands)).

```json
"hackernews": {
  "url": "https://news.ycombinator.com",
  "pagination": { ... },
  "query": {
    "type": "list",
    "selector": "tr.athing.submission",
    "id": { ... },
    "filter": { ... },
    "variables": { ... }
  }
}
```

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `url` | yes | URL to fetch. Supports Liquid variables from rule params, e.g. `https://example.com?q={{query}}` |
| `format` | no | `"html"` (default) or `"xml"`. Use `"xml"` for RSS/Atom feeds or any XML document. Switches BeautifulSoup to the lxml XML parser (requires `lxml`). |
| `userAgent` | no | Custom User-Agent header. If omitted, a default browser-like User-Agent is used. Useful for RSS feeds or APIs that require a specific User-Agent. |
| `params` | no | List of parameter names used in the URL template |
| `pagination` | no | Pagination config (see below) |
| `query.type` | yes | `"list"` (multiple items) or `"single"` (one item) |
| `query.selector` | yes | CSS selector for item container(s). For XML, use XML element names (e.g. `item` for RSS, `entry` for Atom). |
| `query.id` | no | How to extract a unique ID per item (see below) |
| `query.filter` | no | Filter to exclude items (see below) |
| `query.expect` | no | List of CSS selectors that must exist on the page (see [Expected structure](#expected-structure)). Sends error email if missing. |
| `query.reject` | no | List of CSS selectors that indicate no real results (see [Reject selectors](#reject-selectors)). Returns 0 items if any match. |
| `query.variables` | yes | Named fields to extract (see below) |

### `rules` -- What to run

Each rule references a definition and can override params, email recipient, template, etc.

```json
{
  "ref": "hackernews",
  "name": "hackernews",
  "schedule": "0 */6 * * *",
  "subject": "Hacker News: {{count}} new stories",
  "template": "./templates/hackernews",
  "email": "you@example.com"
}
```

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `ref` | yes | Name of the definition in `defs` |
| `name` | yes | Unique rule name. Used for state file (`~/.mutimon/data/<name>`) |
| `schedule` | no | Cron expression or array of expressions (see [Schedule](#schedule)). If omitted, runs every time. |
| `subject` | yes | Liquid template for the email subject line |
| `template` | yes | Path to the Liquid template file (relative to `~/.mutimon/`) |
| `email` | yes | Recipient email address |
| `params` | no | Values for the definition's URL template variables. Used when `input` is not specified. |
| `input` | no | One or more input entries with params and optional validators (see [Multiple inputs](#multiple-inputs)). Overrides `params`. |

## Variable extraction

Each variable in `query.variables` defines how to extract a value from a matched element:

```json
"title": {
  "selector": ".titleline > a",
  "value": {
    "type": "text"
  }
}
```

### Value types

| Type | Description | Extra fields |
|------|-------------|--------------|
| `text` | Inner text of the element | |
| `attribute` | HTML attribute value | `name` -- attribute name (e.g. `"href"`) |
| `html` | Raw inner HTML of the element | Use the `html2text` Liquid filter in templates to convert to plain text |

### Optional value modifiers

| Field | Description |
|-------|-------------|
| `regex` | Extract a capture group from the raw value. Uses group(1) if available. |
| `prefix` | String prepended to the final value. Useful for turning relative URLs into absolute. |
| `parse` | Convert the extracted string to a typed value. `"number"`: plain numeric parsing for integers and floats, strips commas as thousands separators (e.g. `"1,234"` -> `1234`, `"3.14"` -> `3.14`). `"money"`: locale-aware currency parsing via [babel](https://babel.pocoo.org/), auto-detects page language from `<html lang>` or `Content-Language` header, strips currency symbols and percent signs, handles US (`$70,528.40`), European (`11,8000 zł`), and mixed (`11.800,50 €`) formats. `"list"`: split the value into a list using the `delimiter` regex (default `\s*,\s*`), use `{% for x in item.field %}` in templates. `"json"`: parse the value as JSON, then optionally extract structured data with `query` (see [JSON extraction](#json-extraction)). Parsed values are used by validators. |
| `delimiter` | Regex pattern used to split the value when `parse` is `"list"`. Defaults to `\s*,\s*` (comma with optional surrounding whitespace). |
| `query` | Only for `parse: "json"`. Defines how to navigate and extract variables from the parsed JSON using [JMESPath](https://jmespath.org/) (see [JSON extraction](#json-extraction)). |

### Optional variable fields

| Field | Description |
|-------|-------------|
| `default` | Fallback value if the selector doesn't match or the value is empty |
| `sibling` | When `true`, search the next sibling element instead of within the matched element. Needed when data is split across adjacent HTML elements (e.g. Hacker News stores title and score in separate `<tr>` rows). |
| `collect` | When `true`, collect ALL matching elements (using `select()` instead of `select_one()`). Returns a list that can be iterated in templates with `{% for skill in item.skills %}`. Useful for extracting lists of tags, skills, or categories from repeated elements. |
| `find` | Array of chainable DOM traversal steps for advanced element navigation. See [DOM traversal](#dom-traversal-find). |
| `transform` | Array of DOM mutation steps applied before value extraction. See [DOM transformation](#dom-transformation-transform). |

### Special selectors

| Selector | Description |
|----------|-------------|
| `:self` | References the container element itself instead of searching for a child. Useful when the container is an `<a>` tag and you need its `href` attribute. |

### Example with all options

```json
"url": {
  "selector": "a.job__title-link",
  "value": {
    "type": "attribute",
    "name": "href",
    "regex": "^(/.*)",
    "prefix": "https://useme.com"
  }
}
```

This selects the `href` attribute from `a.job__title-link`, extracts the path with a regex, then prepends the domain.

### Collecting multiple values

When an item contains repeated elements (e.g. skill tags, categories), use `collect: true` to extract all matches as a list:

```json
"skills": {
  "selector": ".skill-tag",
  "value": { "type": "text" },
  "collect": true
}
```

This finds all `.skill-tag` elements inside the item container and returns a list like `["TypeScript", "React", "Node.js"]`. Use a loop in the template:

```liquid
{% for skill in item.skills %}{{ skill }}{% unless forloop.last %}, {% endunless %}{% endfor %}
```

### Self-referencing the container

When the container element itself holds the data you need (e.g. an `<a>` tag with an `href`), use `:self`:

```json
"url": {
  "selector": ":self",
  "value": {
    "type": "attribute",
    "name": "href",
    "prefix": "https://example.com"
  }
}
```

## DOM traversal (`find`)

For complex pages where the data you need isn't inside the item container, use `find` to navigate the DOM with chainable traversal steps. This works like jQuery methods — each step takes the result of the previous one as input.

### Traversal methods

| Method | Description |
|--------|-------------|
| `["select", selector]` | Run `select_one()` within the current element |
| `["until", selector]` | Collect next siblings until one matches the selector (inclusive) |
| `["siblings"]` | Collect all next siblings into a fragment |

Steps are applied in order. The result of each step becomes the input for the next.

### Example: Wikipedia discussion threads

Wikipedia discussion pages use DiscussionTools markup where thread content is spread across sibling elements rather than nested inside a container. The `find` chain navigates from the heading to the reply button, collecting everything in between:

```json
"content": {
  "selector": "div.mw-heading2",
  "find": [
    ["until", ".ext-discussiontools-init-replylink-buttons"]
  ],
  "transform": [
    ["remove", ".ext-discussiontools-init-replylink-buttons"],
    ["remove_after", "[data-mw-comment-sig]"]
  ],
  "value": { "type": "html" }
}
```

This selects the heading container, collects all siblings until the reply button, then transforms the result by removing UI elements and stripping signatures before extracting the raw HTML.

## DOM transformation (`transform`)

Use `transform` to modify a DOM fragment before extracting its value. Each step mutates a copy of the element (the original is never modified). Combine with `find` to first locate, then clean up content.

### Transform methods

| Method | Description |
|--------|-------------|
| `["remove", selector]` | Remove all elements matching the CSS selector |
| `["remove_after", selector]` | Remove the first match and all following siblings in its parent |

### Example: stripping signatures

Wikipedia comments end with signature markup. Use `remove_after` to cut everything from the signature marker onward:

```json
"transform": [
  ["remove", ".reply-button"],
  ["remove_after", "[data-mw-comment-sig]"]
]
```

## HTML value type and `html2text` filter

Use `type: "html"` to extract the raw inner HTML of an element instead of plain text. This preserves the full markup including links, code blocks, and formatting.

To convert HTML to readable plain text in email templates, use the built-in `html2text` Liquid filter:

```liquid
{{ item.content | html2text | truncate: 1000 }}
```

The `html2text` filter converts HTML to Markdown-like plain text, preserving code blocks and link URLs. The `truncate` filter can be chained to limit output length.

## JSON extraction

Some websites embed structured data as JSON inside `<script>` tags (e.g. Next.js apps use `<script id="__NEXT_DATA__">`). When the HTML elements don't contain all the data you need, you can extract it from the embedded JSON instead.

Use `parse: "json"` combined with a `query` to navigate the JSON structure using [JMESPath](https://jmespath.org/) expressions.

### Basic structure

```json
"locations": {
  "selector": "script#__NEXT_DATA__",
  "value": {
    "type": "text",
    "parse": "json",
    "query": {
      "type": "list",
      "path": "props.pageProps.data.items[?id == `{{id}}`].offers[]",
      "variables": {
        "city": { "path": "displayWorkplace" },
        "url": { "path": "offerAbsoluteUri" }
      }
    }
  }
}
```

**How it works:**

1. `selector` selects the element containing JSON (e.g. a `<script>` tag) — standard CSS selector
2. `type: "text"` extracts the text content — same as any other variable
3. `parse: "json"` parses the text as a JSON object
4. `query` navigates the parsed JSON and extracts variables:

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | `"list"` (returns array of objects) or `"single"` (returns one object) |
| `path` | no | [JMESPath](https://jmespath.org/) expression to navigate the JSON. Supports Liquid variables (`{{id}}`, `{{name}}`, etc.) rendered against the current item's data. If omitted, the root JSON object is used. |
| `variables` | yes | Named fields to extract from each result. Each has a `path` (JMESPath sub-expression). |

The `path` supports Liquid variable interpolation, so you can match JSON entries to the current HTML item. For example, `{{id}}` is replaced with the item's extracted ID before the JMESPath query runs.

### JMESPath syntax

JMESPath is a query language for JSON. Common patterns:

| Expression | Description |
|------------|-------------|
| `foo.bar.baz` | Navigate nested objects |
| `items[0]` | Array index |
| `items[*].name` | Get `name` from all array entries |
| `items[?id == \`123\`]` | Filter: entries where `id` equals `123` |
| `items[?score > \`50\`]` | Filter: entries where `score` > 50 |
| `items[].offers[]` | Flatten nested arrays |

Note: literal values in JMESPath filters use backticks (`` ` ``), not quotes. See the [JMESPath tutorial](https://jmespath.org/tutorial.html) for full syntax.

### Template usage

When `query.type` is `"list"`, the variable is a list of objects accessible in templates:

```liquid
{% for loc in item.locations %}
* {{ loc.city }}: {{ loc.url }}
{% endfor %}
```

When `query.type` is `"single"`, the variable is a flat object:

```liquid
{{ item.metadata.author }} - {{ item.metadata.date }}
```

### Works with attributes too

JSON can also appear in HTML attributes. Use `type: "attribute"` with `parse: "json"`:

```json
"config": {
  "selector": "[data-config]",
  "value": {
    "type": "attribute",
    "name": "data-config",
    "parse": "json",
    "query": {
      "type": "single",
      "variables": {
        "status": { "path": "status" },
        "count": { "path": "meta.count" }
      }
    }
  }
}
```

### Example: Next.js multi-location job offers

Pracuj.pl (a Next.js app) lists job offers with multi-location variants. The HTML card only shows the title, but the city-specific URLs are in `__NEXT_DATA__`:

```json
"url_list": {
  "selector": "script#__NEXT_DATA__",
  "value": {
    "type": "text",
    "parse": "json",
    "query": {
      "type": "list",
      "path": "props.pageProps.dehydratedState.queries[0].state.data.groupedOffers[?offers[0].partitionId == `{{id}}`].offers[]",
      "variables": {
        "city": { "path": "displayWorkplace" },
        "url": { "path": "offerAbsoluteUri" }
      }
    }
  }
}
```

The `{{id}}` in the path is the item's ID extracted from the HTML (`data-test-offerid` attribute). JMESPath filters the `groupedOffers` array to find the matching entry, then flattens its `offers[]` sub-array. Each offer's `displayWorkplace` and `offerAbsoluteUri` are extracted as `city` and `url`.

## Item identity (deduplication)

The `id` field in the query spec controls how the scraper identifies items it has already seen.

### From a variable with regex

```json
"id": {
  "source": "url",
  "regex": ",(\\d+)/$"
}
```

Takes the `url` variable value and extracts the ID using a regex. The `source` can reference either a variable name (from `variables`) or a param name (from `input`/`params`). When using `input`, params are merged into items before ID extraction, so `"source": "symbol"` works if `symbol` is a param.

### From an HTML attribute

```json
"id": {
  "type": "attribute",
  "name": "id"
}
```

Reads the `id` attribute directly from the matched element (e.g. `<tr id="47415919">`).

### Fallback

If no `id` spec is provided, the `url` variable is used as the identity. If there's no `url` either, a hash of all variables is used.

## Filtering

The `filter` field excludes items based on CSS class:

```json
"filter": {
  "selector": ".job__header-details--date",
  "exclude_class": "job__header-details--closed"
}
```

This finds `.job__header-details--date` within each item and skips the item if it has the class `job__header-details--closed`. Items where the filter selector doesn't match any element are also excluded.

## Expected structure

The `expect` field on a query spec lists CSS selectors that must exist on the page. If any are missing, the scraper sends an error email about HTML structure changes instead of silently producing empty results.

```json
"query": {
  "expect": [".text-center img[alt='Linux']", ".pagination"],
  "selector": "...",
  ...
}
```

This is checked on the first page only. Useful for detecting when a website redesigns and your selectors break.

## Reject selectors

The `reject` field is the inverse of `expect` — it lists CSS selectors that indicate the page has **no real results**. If any selector matches, the page returns 0 items. This is useful for sites that show recommended or unrelated content when there are no actual matches for the search query.

```json
"query": {
  "reject": ["nfj-no-offers-found-header"],
  "selector": "...",
  ...
}
```

For example, nofluffjobs.com shows a "Brak wyników wyszukiwania" message and recommended jobs when a language has no remote offers. The `reject` selector detects the no-results element and prevents those recommendations from being treated as real results.

## Multiple inputs

The `input` field allows a single rule to scrape multiple pages with different parameters and combine the results into one email. This is useful for monitoring multiple items on the same website (e.g. multiple stock symbols).

`input` can be a single object or an array:

```json
{
  "ref": "bankier",
  "name": "akcje",
  "subject": "[bankier.pl] Zmiany Akcji",
  "template": "./templates/bankier",
  "email": "you@example.com",
  "input": [
    { "params": { "symbol": "BIOMAXIMA" }, "validator": { "test": "{{price}} > 10" } },
    { "params": { "symbol": "AGORA" }, "validator": { "test": "{{price}} > 9.5" } },
    { "params": { "symbol": "ASSECOPOL" } },
    { "params": { "symbol": "POLTREG" } }
  ]
}
```

Each entry fetches the URL with its own `params`. If `input` is omitted, the rule's `params` field is used directly (backward compatible).

Params from each input entry are merged into the extracted items, so they're available in templates (e.g. `{{symbol}}`).

### Input expansion with `each`

When multiple inputs share the same structure and only differ by one parameter, use `each` to avoid repeating the same object:

```json
"input": {
  "each": { "var": "subreddit", "values": ["Python", "JavaScript", "scheme"] },
  "params": { "feed_url": "https://www.reddit.com/r/{{subreddit}}/new.rss" },
  "validator": { "@id": "hiring-posts" }
}
```

This expands into three input entries, one per value, each with its own `params` where `{{subreddit}}` is replaced. The `validator` (or `track`) is shared across all entries.

The `each.values` array can also contain objects, accessed via dot notation:

```json
"input": {
  "each": {
    "var": "data",
    "values": [
      { "category": "electronics", "type": "phones" },
      { "category": "computers", "type": "laptops" }
    ]
  },
  "params": { "url": "https://example.com/{{data.category}}/type/{{data.type}}" }
}
```

## Validators

Each input entry can have a `validator` object that filters extracted items. The validator supports two condition types. If both are present, both must pass (AND logic).

### `test` -- Numeric expression

A [numexpr](https://numexpr.readthedocs.io/) expression with Liquid variable placeholders. Variables should use `"parse": "number"` or `"parse": "money"` in the definition so they're available as floats.

```json
"validator": {
  "test": "{{price}} > 9.5"
}
```

Supported operations:

| Operator | Example |
|----------|---------|
| Comparison | `{{price}} > 10`, `{{change_pct}} <= -5` |
| AND | `({{price}} > 10) & ({{change_pct}} < 0)` |
| OR | `({{price}} < 5) \| ({{price}} > 100)` |
| Arithmetic | `{{price}} * {{quantity}} > 1000` |
| Functions | `abs({{change_pct}}) > 3` |

Use parentheses to group compound expressions. See the [numexpr documentation](https://numexpr.readthedocs.io/) for the full list of supported operations.

### `match` -- Regex match

Matches a variable value against a regex pattern. Uses `re.search()` so the pattern matches anywhere unless anchored with `^` or `$`.

```json
"validator": {
  "match": {
    "var": "title",
    "regex": "^Ask HN"
  }
}
```

**Match condition fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `var` | one of `var` or `value` | Direct variable name — returns the raw value, preserving lists from `collect: true` |
| `value` | one of `var` or `value` | Liquid template string rendered against item variables (always produces a string) |
| `regex` | one of `regex`, `include`, or `exclude` | Regex pattern tested with `re.search()` (matches anywhere unless anchored). For list values, elements are joined with `", "` before matching. |
| `include` | one of `regex`, `include`, or `exclude` | Array of strings — passes if any string is found (see below) |
| `exclude` | one of `regex`, `include`, or `exclude` | Array of strings — passes if none are found (see below) |
| `strict` | no | When `true`, `include`/`exclude` use exact string equality instead of substring match. Only affects string values — list values always use exact element matching. Default `false`. |
| `exist` | no | Whether the regex pattern should exist. Default `true`. Set to `false` to pass when the regex does NOT match. Not needed with `exclude`. |

Set `"exist": false` to pass when the pattern is **not found**. This is useful for detecting when something disappears from a page:

```json
"validator": {
  "match": {
    "var": "status",
    "regex": "Coming soon",
    "exist": false
  }
}
```

### `include` / `exclude` -- String list match

Use `include` or `exclude` instead of `regex` when checking against a list of plain strings. Use `var` to reference the variable directly — when the variable is a list (from `collect: true`), each element is compared as an exact match, so `"Java"` will match the skill `"Java"` but not `"JavaScript"`. For plain string values, substring matching is used by default (use `strict: true` for exact matching).

```json
"validator": {
  "match": {
    "var": "skills",
    "exclude": ["Angular", "C#", ".NET", "Java"]
  }
}
```

`match` can also be an array of match objects (AND logic — all must pass):

```json
"validator": {
  "match": [
    { "var": "platform", "regex": "Linux" },
    { "var": "status", "regex": "Coming soon", "exist": false }
  ]
}
```

### Combined example

Both conditions must pass (AND logic within a single object):

```json
"validator": {
  "test": "{{price}} > 80",
  "match": {
    "var": "company",
    "regex": "Asseco"
  }
}
```

### Array of validators (OR logic)

The validator can also be an array. The item is included if **any** validator in the array passes. This is useful for defining price thresholds or notification steps:

```json
"validator": [
  { "test": "{{price}} > 8" },
  { "test": "{{price}} > 9" },
  { "test": "{{price}} > 9.5" }
]
```

Each entry in the array is a full validator object that can use `test`, `match`, or both.

### Required validators (`require`)

In a validator array, set `"require": true` to make a validator mandatory. Required validators must ALL pass (AND logic), while the remaining validators use OR logic (at least one must pass). If only required validators exist, the OR check is skipped.

This is useful for combining a baseline filter with threshold alerts:

```json
"validator": [
  { "require": true, "test": "{{score_num}} > 50" },
  { "test": "{{price}} > 75000" },
  { "test": "{{price}} > 80000" },
  { "test": "{{price}} > 100000" }
]
```

The `require` validator acts as a gate — items must pass it before the OR thresholds are even considered.

### Reusable validators (`@id`)

Define shared validators in `defs.validators` and reference them by name using `{"@id": "name"}`. This eliminates duplication when multiple rules use the same filter:

```json
"defs": {
  "validators": {
    "job-board": {
      "require": true,
      "match": [
        {"var": "title", "exclude": ["Angular", "C#", ".NET"]},
        {"var": "skills", "exclude": ["Angular", "C#", ".NET", "Java"]}
      ]
    }
  }
}
```

Then reference it in rules:

```json
"input": {
  "validator": {"@id": "job-board"}
}
```

`@id` references work anywhere a validator is expected — as a standalone validator, or as an element in a validator array:

```json
"validator": [
  {"@id": "job-board"},
  { "require": true, "match": { "var": "salary", "regex": "Undisclosed", "exist": false } }
]
```

## Commands

Commands are reusable Liquid tags defined in `defs.commands`. Each command becomes a custom `{% tag %}` that can be used in validator `test` and `match` expressions, replacing verbose Liquid expressions with short, readable tags.

### Defining commands

Commands are defined in the `commands` key under `defs`:

```json
"defs": {
  "commands": {
    "fresh": {
      "args": ["field", "seconds"],
      "template": "{{ field | date: \"%s\" }} > {{ \"now\" | date: \"%s\" | minus: seconds }}"
    },
    "today": {
      "args": ["field"],
      "template": "{{ field | date: \"%Y%m%d\" }} == {{ \"now\" | date: \"%Y%m%d\" }}"
    }
  },
  ...
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `args` | no | Ordered list of argument names. Values are passed positionally when the tag is used. |
| `template` | yes | Liquid template string rendered with bound arguments. Argument names are available as variables. |

### Using commands

Use commands as `{% name arg1 arg2 %}` in any validator `test` or `match` expression:

```json
"validator": {
  "test": "{% fresh date 604800 %}"
}
```

This is equivalent to writing the full Liquid expression:

```json
"test": "{{ date | date: \"%s\" }} > {{ \"now\" | date: \"%s\" | minus: 604800 }}"
```

Arguments are matched positionally to the `args` list in the command definition. Word arguments (like `date`) are resolved as variables from the item context. Numeric arguments (like `604800`) are passed as literal values.

### Built-in commands in skeleton

The skeleton config includes two commands:

**`{% fresh <field> <seconds> %}`** — checks whether a date field is newer than a given number of seconds. Useful for filtering stale items from feeds that return non-deterministic results:

```json
"input": {
  "validator": {
    "test": "{% fresh date 604800 %}"
  }
}
```

This filters out any items where the `date` field is older than 7 days (604800 seconds).

**`{% today <field> %}`** — checks whether a date field matches today's date:

```json
"input": {
  "validator": {
    "require": true,
    "test": "{% today date %}"
  }
}
```

## Filters

Custom Liquid filters defined in `defs.filters`. Each key becomes a filter usable as `{{ value | name }}` in templates. Filters are defined using standard Liquid filter expression syntax — the input value is piped through the expression chain.

### Defining filters

```json
"defs": {
  "filters": {
    "clean": "replace_regex: '\\s+', ' ' | strip"
  }
}
```

The expression uses standard Liquid pipe syntax. Built-in Liquid filters (`strip`, `downcase`, `replace`, etc.) and the additional `replace_regex` filter are available:

| Filter | Description |
|--------|-------------|
| `replace_regex: pattern, replacement` | Regex substitution (supports backreferences `\1`, `\2`, etc.) |
| `html2text` | Convert HTML to plain text, preserving code blocks and link URLs |

Filters can be chained with `|` — the output of one becomes the input of the next. Custom filters can also reference other custom filters defined earlier.

### Using filters

Use filters with the standard Liquid pipe syntax in any template:

```
{{ item.snippet | clean }}
```

The `clean` filter above collapses all whitespace (newlines, tabs, spaces) into a single space and trims leading/trailing whitespace.

## Pagination

Two pagination types are supported:

### `next_link` -- Follow a "next" link

For sites with a single "More" or "Next" link (e.g. Hacker News):

```json
"pagination": {
  "type": "next_link",
  "selector": "a.morelink",
  "base_url": "https://news.ycombinator.com/",
  "max_pages": 2
}
```

### `numbered` -- Follow numbered page buttons

For sites with numbered pagination (e.g. useme.com):

```json
"pagination": {
  "type": "numbered",
  "selector": ".pagination .pagination__page",
  "active_class": "pagination__page--active",
  "base_url": "https://useme.com/pl/jobs/",
  "max_pages": 5
}
```

Finds the active page button and follows the link of the next one.

### Common fields

| Field | Required | Description |
|-------|----------|-------------|
| `max_pages` | no | Maximum number of pages to fetch (default: 1) |
| `base_url` | no | Base URL for resolving relative `href` values |

## Schedule

Each rule can have a `schedule` field with a standard cron expression or an array of expressions (any match triggers the rule). The script is designed to be invoked frequently (e.g. every 5 minutes via system cron), and it decides internally which rules are due based on their schedule.

The schedule uses [croniter](https://github.com/kiorky/croniter) to parse standard 5-field cron expressions:

```
 ┌───────────── minute (0-59)
 │ ┌───────────── hour (0-23)
 │ │ ┌───────────── day of month (1-31)
 │ │ │ ┌───────────── month (1-12)
 │ │ │ │ ┌───────────── day of week (0-7, 0 and 7 are Sunday)
 │ │ │ │ │
 * * * * *
```

### Examples

| Expression | Meaning |
|------------|---------|
| `0 8 * * *` | Daily at 8:00 |
| `0 */6 * * *` | Every 6 hours (0:00, 6:00, 12:00, 18:00) |
| `0 9 * * 1` | Every Monday at 9:00 |
| `*/30 * * * *` | Every 30 minutes |
| `0 8,20 * * *` | Twice daily at 8:00 and 20:00 |

### Array of schedules

When a single cron expression can't cover your needs, use an array. The rule runs if **any** expression matches:

```json
"schedule": ["0,30 9 * * *", "0 16 * * *"]
```

This runs at 9:00, 9:30, and 16:00 — something not expressible in a single 5-field cron string.

### How it works

The script is designed to be invoked periodically by system cron (e.g. every 5 minutes or every hour). On each invocation:

1. The current time is truncated to the start of the minute (e.g. 14:03:27 becomes 14:03:00)
2. Each rule's cron expression is checked against that time using `croniter.match`
3. If it matches and the rule hasn't already run in this minute window, it executes
4. After a successful run, a timestamp is saved to `~/.mutimon/data/.lastrun_<rule_name>` to prevent duplicate runs if the script is triggered again within the same minute
5. If no schedule is set, the rule runs every time
6. Use `--force` to bypass all schedules

## Email templates

Templates use [Liquid](https://shopify.github.io/liquid/) syntax via [python-liquid](https://github.com/jg-rp/liquid). The following variables are available:

| Variable | Description |
|----------|-------------|
| `{{ count }}` | Number of new items |
| `{{ now }}` | Current date and time |
| `{{ search_url }}` | The rendered URL from the definition |
| `{% for item in items %}` | Loop over new items |
| `{{ item.index }}` | 1-based position within the items list |
| Any rule `params` | e.g. `{{ query }}` |
| Any extracted variable | e.g. `{{ item.title }}`, `{{ item.url }}`, `{{ item.score }}` |

Liquid supports conditionals, filters, and logic — see the [Liquid docs](https://shopify.github.io/liquid/).

### Example template

```
Hacker News - New Stories
Checked at: {{ now }}

Number of new stories: {{ count }}
============================================================
{% for item in items %}

{{ item.rank }} {{ item.title }}
     Score: {{ item.score }} point{% if item.score != 1 %}s{% endif %} | {{ item.age }}
     URL:   {{ item.url }}
     HN:    {{ item.comments_url }}
{% endfor %}

============================================================
```

The `subject` field in a rule is also a Liquid template with access to the same variables.

## How it works

1. On each run, all rules in the config are processed sequentially
2. For each rule, the scraper fetches the URL (with pagination) and extracts items using CSS selectors
3. For `parse: "money"`, the page language is detected from `<html lang>` or the `Content-Language` header, and used for locale-aware currency parsing via [babel](https://babel.pocoo.org/)
4. Items are compared against the saved state in `~/.mutimon/data/<rule_name>`
5. New items, or items that crossed a validator threshold (previously failed, now pass), trigger an email notification
6. ALL items are saved in state with a `_valid` flag (or `_state` index for track mode), so threshold crossings are detected on subsequent runs

## Threshold crossing detection

When a rule has validators, the scraper tracks whether each item passed or failed on the previous run. This enables re-notifications when a value crosses a threshold boundary:

1. **Price rises to $75k** → validator `>= 75000` passes → notify, save `_valid: true`
2. **Price drops to $72k** → validator fails → no notification, save `_valid: false`
3. **Price rises to $76k** → validator passes, previous `_valid` was false → **notify again**
4. **Price stays at $76k** → validator passes, previous `_valid` was true → no notification

This works for both upward thresholds (`>=`) and downward thresholds (`<=`). The state file stores all fetched items (not just those passing the validator) with a `_valid` boolean.

## State-machine tracking (`track`)

For more granular threshold monitoring, use `track` instead of `validator` on an input entry. While `validator` stores a single pass/fail boolean, `track` implements a state machine that tracks *which* threshold an item is in and notifies on every state transition.

`track` and `validator` are mutually exclusive on the same input entry.

### Configuration

```json
"input": {
  "params": { "symbol": "ASSECOPOL" },
  "track": {
    "value": "{{price}}",
    "states": [
      { "test": "{{price}} > 200", "name": "above 200 zł" },
      { "test": "{{price}} > 190", "name": "above 190 zł" },
      { "test": "{{price}} > 180", "name": "above 180 zł" },
      { "test": "{{price}} <= 180", "silent": true }
    ]
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `value` | no | Liquid expression to evaluate and save as `_value` for templates. |
| `states` | yes | Array of state definitions, evaluated top-down. First matching state wins. |
| `states[].test` | yes | numexpr expression with Liquid variables. |
| `states[].name` | no | Human-friendly label, available as `{{ item._state_name }}` in templates. Defaults to the `test` expression. |
| `states[].silent` | no | If `true`, transitioning to this state saves state but does not trigger a notification. Default `false`. |

### How it works

States are evaluated top-down — the first matching `test` expression determines the item's current state (by index). On each run:

1. **New item** — notify if the current state is not `silent`
2. **State changed** (index differs from previous run) — notify if the new state is not `silent`
3. **State unchanged** — no notification

### Example: stock price monitoring

```json
"track": {
  "value": "{{price}}",
  "states": [
    { "test": "{{price}} > 200", "name": "above 200 zł" },
    { "test": "{{price}} > 190", "name": "above 190 zł" },
    { "test": "{{price}} > 180", "name": "above 180 zł" },
    { "test": "{{price}} <= 180", "silent": true }
  ]
}
```

| Run | Price | State | Previous | Notify? |
|-----|-------|-------|----------|---------|
| 1 | 185 | above 180 zł (2) | — | Yes (new) |
| 2 | 188 | above 180 zł (2) | above 180 zł (2) | No (same state) |
| 3 | 195 | above 190 zł (1) | above 180 zł (2) | **Yes** (crossed 190) |
| 4 | 170 | silent (3) | above 190 zł (1) | No (silent) |
| 5 | 185 | above 180 zł (2) | silent (3) | **Yes** (came back) |
| 6 | 195 | above 190 zł (1) | above 180 zł (2) | **Yes** (crossed 190 again) |

### Template variables

In track mode, the following variables are available in templates:

| Variable | Description |
|----------|-------------|
| `{{ item._state_name }}` | Name (or test expression) of the current state |
| `{{ item._prev_state_name }}` | Name of the previous state (or empty for new items) |
| `{{ item._value }}` | Rendered value from `track.value` (e.g. the current price) |

### When to use `track` vs `validator`

- **`validator`** — binary filter: include/exclude items. Good for "notify me about new Hacker News posts with score > 100" or "exclude job offers with Angular".
- **`track`** — state machine: notify on every threshold crossing. Good for "notify me each time ASSECOPOL crosses above 190 zł, and again when it crosses above 200 zł".

## Error handling

The scraper sends error emails for four types of failures. The error email function (`send_error_email`) uses only Python's standard library (no third-party deps), so it works even when the error is caused by a missing dependency.

| Error | Email subject | Behavior |
|-------|--------------|----------|
| Missing dependency (e.g. `import liquid` fails) | `[mutimon] Missing dependency` | Sends traceback, exits |
| Invalid config (schema validation fails) | `[mutimon] Invalid configuration` | Sends all validation errors, exits |
| HTML structure change (`expect` selectors missing) | `[mutimon] HTML structure changed for '<rule>'` | Sends missing selectors, skips that input, continues other rules |
| Fatal runtime crash (unhandled exception in `main()`) | `[mutimon] Fatal error` | Sends full traceback |

Error emails are sent to all unique recipient addresses found across all rules in the config.

## Examples

The `skeleton/` directory contains ready-to-use examples that are copied to `~/.mutimon/` on first run.

### Hacker News — New stories

Monitors the Hacker News front page for new stories. Uses pagination to fetch 2 pages (60 stories), sibling element extraction for scores, and `data-test` attribute-based IDs.

**Files:** `skeleton/config.json` (hackernews def + rule), `skeleton/templates/hackernews`

### Bitcoin price alerts — CoinMarketCap

Monitors Bitcoin price on CoinMarketCap with threshold-based alerts. Demonstrates:

- **Price going up**: notify when Bitcoin crosses above $75k, $80k, $90k, $100k
- **Price going down**: notify when Bitcoin drops below $60k, $50k, $40k
- **Threshold crossing detection**: if price rises above $75k (notify), drops to $72k (no notify), then rises back above $75k (notify again)
- **Locale-aware money parsing**: `$70,528.40` is correctly parsed as `70528.40` using `parse: "money"` (US English format detected from `<html lang="en">`)
- **Structure validation**: `expect` field checks that `[data-test='text-cdp-price-display']` exists on the page

**Files:** `skeleton/config.json` (coinmarketcap def + rule), `skeleton/templates/coinmarketcap`

The bitcoin rule uses two input entries — one for upward thresholds (`>=`), one for downward thresholds (`<=`):

```json
"input": [
  {
    "params": { "coin": "bitcoin" },
    "validator": [
      { "test": "{{price}} >= 75000" },
      { "test": "{{price}} >= 80000" },
      { "test": "{{price}} >= 100000" }
    ]
  },
  {
    "params": { "coin": "bitcoin" },
    "validator": [
      { "test": "{{price}} <= 60000" },
      { "test": "{{price}} <= 50000" }
    ]
  }
]
```

> **Tip:** For more granular notifications (e.g. notify each time the price crosses a specific level, not just when it re-enters a "passing" state), use [`track`](#state-machine-tracking-track) instead of `validator`.

### Stock price tracking — State machine

Monitors stock prices with per-threshold notifications using `track`. Unlike the Bitcoin example which uses validator (binary pass/fail), `track` notifies on every state transition — e.g. when a stock crosses above 190 zł, then again when it crosses 200 zł.

```json
"input": [
  {
    "params": { "symbol": "ASSECOPOL" },
    "track": {
      "value": "{{price}}",
      "states": [
        { "test": "{{price}} > 200", "name": "above 200 zł" },
        { "test": "{{price}} > 190", "name": "above 190 zł" },
        { "test": "{{price}} > 180", "name": "above 180 zł" },
        { "test": "{{price}} <= 180", "silent": true }
      ]
    }
  }
]
```

The `silent` state at the bottom acts as a "reset" — when the price drops below 180, no notification is sent, but the state is saved. When the price rises back above 180, it's detected as a state change and triggers a new notification.

### Reddit subreddit — RSS/Atom feed

Monitors a Reddit subreddit via its Atom feed (Reddit serves `.rss` URLs as Atom XML). Demonstrates:

- **XML format**: `format: "xml"` switches from HTML to XML parsing, so CSS selectors target XML elements (`entry`, `title`, `link`) instead of HTML
- **Custom User-Agent**: Reddit blocks default scrapers, so a Liferea RSS reader User-Agent is used
- **Parameterized subreddit**: the `subreddit` param lets the same definition monitor any subreddit
- **Atom-specific selectors**: `entry` for items, `link[href]` for URLs (Atom uses `<link href="..."/>` instead of `<link>text</link>`)

**Files:** `skeleton/config.json` (reddit-atom def + rule), `skeleton/templates/reddit`

```json
"reddit-atom": {
  "params": ["subreddit"],
  "format": "xml",
  "userAgent": "Liferea/1.15.6 (Linux; https://lzone.de/liferea/) AppleWebKit (KHTML, like Gecko)",
  "url": "https://www.reddit.com/r/{{subreddit}}.rss",
  "query": {
    "type": "list",
    "selector": "entry",
    "id": { "source": "entry_id" },
    "variables": {
      "title":    { "selector": "title", "value": { "type": "text" } },
      "url":      { "selector": "link", "value": { "type": "attribute", "name": "href" } },
      "entry_id": { "selector": "id", "value": { "type": "text" } },
      "date":     { "selector": "updated", "value": { "type": "text" }, "default": "" },
      "author":   { "selector": "author name", "value": { "type": "text" }, "default": "" }
    }
  }
}
```

### Multiple Reddit subreddits — Hiring posts with `each`

Monitors multiple subreddits for posts about hiring Python or JavaScript developers. Demonstrates:

- **`each` expansion**: a single `input` object expands into multiple fetches, one per subreddit — no need to repeat the same params/validator for each
- **Reusable validator**: `defs.validators` defines a named validator referenced by `@id` across all expanded entries
- **Combined results**: all matching posts from all subreddits land in one email

**Definition:** uses the same `reddit-atom` def from the previous example.

**Reusable validator:**
```json
"validators": {
  "reddit-hiring": [
    { "test": "{% fresh date 604800 %}", "require": true },
    { "match": { "var": "title", "regex": "(?i)\\b(hiring|hire|looking for)\\b.*(Python|JavaScript)" } }
  ]
}
```

**Rule:**
```json
{
  "ref": "reddit-atom",
  "name": "reddit-hiring-dev",
  "schedule": "0 */4 * * *",
  "subject": "[Reddit] {{count}} new hiring post(s)",
  "template": "./templates/reddit",
  "email": "you@example.com",
  "input": {
    "each": { "var": "subreddit", "values": ["Python", "JavaScript"] },
    "params": { "subreddit": "{{subreddit}}" },
    "validator": { "@id": "reddit-hiring" }
  }
}
```

**Files:** `skeleton/config.json` (reddit-atom def + rule), `skeleton/templates/reddit`

## Configuring with AI

Mutimon ships with an AI instruction file that teaches any AI assistant how to add websites. Get its path with:

```bash
mon --ai-guide
```

Use it with Claude Code in batch mode:

```bash
claude -p "$(mon --ai-guide) Add https://github.com/trending to mutimon. Extract repo name, description, URL, language, and stars. Email me daily at 8am at user@example.com."
```

Or with any AI assistant — just paste the output of `mon --ai-guide` as context along with your request.

### Example prompts

### Monitor a website for new content

> Add a rule to monitor Hacker News (https://news.ycombinator.com) for new stories.
> Extract the title, URL, score, and age. Send me an email every 6 hours
> at user@example.com with the new stories.
> Read the AI guide with `mon --ai-guide` for config format reference.

### Price alerts with thresholds

> Add Bitcoin price monitoring using https://coinmarketcap.com/currencies/bitcoin/.
> Notify me when the price crosses above $75,000 or drops below $60,000.
> Check every 4 hours. Send alerts to user@example.com.
> Read the AI guide with `mon --ai-guide` for config format reference.

### Stock price tracking with state machine

> Monitor ASSECOPOL stock on https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=ASSECOPOL.
> Use `track` (not `validator`) to notify me each time the price crosses above 180, 190, or 200 zł.
> Add a silent state for below 180 so I only get notified when it rises back above thresholds.
> Check twice daily during market hours.
> Read the AI guide with `mon --ai-guide` for config format reference.

### Monitor for a feature release

> Monitor https://soloterm.com/download for Linux support. The page currently shows
> "Coming soon" next to Linux. Notify me when that label disappears (use the match
> validator with exist: false). Also add an expect check so I get an error email
> if the page structure changes.
> Read the AI guide with `mon --ai-guide` for config format reference.

### Monitor an RSS/Atom feed

> Add a rule to monitor the r/scheme subreddit via its RSS feed at
> https://www.reddit.com/r/scheme.rss. Reddit serves Atom XML, so use
> format "xml" and a Liferea User-Agent. Extract the title, URL, author,
> and date. Check every 6 hours and email me at user@example.com.
> Read the AI guide with `mon --ai-guide` for config format reference.

### Filter content with regex

> Add a rule to monitor Hacker News for "Ask HN" posts only. Use the existing
> hackernews definition with a match validator that filters titles starting with
> "Ask HN". Read the AI guide with `mon --ai-guide` for config format reference.

### Filter RSS feed with a command

> Add a rule to monitor the r/scheme subreddit via its Atom feed. Use the
> `{% fresh date 604800 %}` command to filter out posts older than 7 days,
> since Reddit's feed sometimes returns stale posts.
> Read the AI guide with `mon --ai-guide` for config format reference.

### Monitor multiple subreddits with `each`

> Monitor r/Python and r/JavaScript subreddits for posts about hiring developers.
> Use the `each` input expansion to avoid duplicating the input entry for each
> subreddit. Filter titles containing "hiring", "hire", or "looking for" combined
> with "Python" or "JavaScript". Use a reusable validator in `defs.validators`.
> Check every 4 hours.
> Read the AI guide with `mon --ai-guide` for config format reference.

### Extract data from Next.js JSON (embedded JSON)

> Add a rule to monitor job offers on https://it.pracuj.pl. The site is a
> Next.js app — some data (like city-specific URLs for multi-location offers)
> is only in the `<script id="__NEXT_DATA__">` JSON, not in the HTML. Use
> `parse: "json"` with a JMESPath query to extract city and URL from the
> embedded JSON.
> Read the AI guide with `mon --ai-guide` for config format reference.

### Extract content from complex page structure

> Add a rule to monitor a Wikipedia discussion page for new threads. The page
> uses MediaWiki DiscussionTools where thread content is spread across sibling
> elements, not nested in a container. Use `find` with `until` to collect
> siblings between headings and reply buttons, `transform` to strip signatures
> and UI elements, and `type: "html"` with the `html2text` Liquid filter to
> convert the content to plain text in the template.
> Read the AI guide with `mon --ai-guide` for config format reference.

## Acknowledge

The logo was created as a combination of clipart from OpenClipart:
* [Time Attendance](https://openclipart.org/detail/315307/time-attendance) by petr.gajdusek
* [Notification logo](https://openclipart.org/detail/271120/notification-logo) by laftello
* [Internet Graphic Chart 02](https://openclipart.org/detail/239851/1brooverview) by cset_paper

It also uses [Lovelo](https://www.dafontfree.io/lovelo-font-free/) font.

## Name Origin

**Mutimon** is a concise Latin portmanteau formed from mutare (“to change”) + monere (“to warn / monitor”).

## License

Copyright (C) 2026 [Jakub T. Jankiewicz](https://jakub.jankiewicz.org)<br/>
Released under [GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.en.html) license
