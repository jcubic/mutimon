# Mutimon — AI Guide for Adding Websites

You are configuring Mutimon, a config-driven web scraper. All config lives in `~/.mutimon/config.json`.
Your job: add a new website to monitor by editing that config file and creating a template.

## What you need to do

1. Fetch the target page HTML and inspect its structure
2. Add a **definition** to `defs` (how to scrape)
3. Add a **rule** to `rules` (when to scrape, who to email)
4. Create a **Liquid template** file in `~/.mutimon/templates/`
5. Validate: run `mon --validate`
6. Test: run `mon --dry-run --force <rule_name>` to verify extraction works

## Definition structure (`defs`)

```json
"my-site": {
  "url": "https://example.com/page",
  "query": {
    "type": "list",
    "selector": "CSS selector for each item container",
    "id": {
      "type": "attribute",
      "name": "data-id"
    },
    "variables": {
      "title": {
        "selector": "h3.title",
        "value": { "type": "text" }
      },
      "url": {
        "selector": "a",
        "value": { "type": "attribute", "name": "href", "prefix": "https://example.com" }
      }
    }
  }
}
```

### Key fields

- `url` — page URL, supports `{{param}}` Liquid variables for parametrized URLs
- `format` — `"html"` (default) or `"xml"` for RSS/Atom feeds
- `userAgent` — custom User-Agent string (some feeds/APIs block default agents)
- `query.type` — `"list"` (multiple items) or `"single"` (one value per page)
- `query.selector` — CSS selector for item containers
- `query.id` — how to get a unique ID: `{"type": "attribute", "name": "..."}` or `{"source": "url", "regex": "..."}`
- `query.expect` — array of CSS selectors that must exist (error email if missing, catches page redesigns)
- `query.reject` — array of CSS selectors that mean "no real results" (returns 0 items if any match)

### Variable extraction

Each variable needs a `selector` and `value`:

```json
"varname": {
  "selector": "CSS selector relative to item container",
  "value": { "type": "text" }
}
```

Value types:
- `{"type": "text"}` — text content of the element
- `{"type": "attribute", "name": "href"}` — attribute value

Options:
- `"prefix": "https://..."` — prepend to the value (for relative URLs)
- `"regex": "(\\d+)"` — extract a capture group from the value
- `"parse": "number"` — parse as float (for numeric comparisons in validators)
- `"parse": "money"` — locale-aware currency parsing (strips currency symbols)
- `"default": ""` — fallback when element not found (prevents errors)
- `"collect": true` — extract ALL matching elements as a list (e.g. tags, skills)
- `"selector": ":self"` — reference the container element itself
- `"sibling": true` — search in the next sibling element (e.g. Hacker News score is in a different `<tr>`)

## Rule structure (`rules`)

```json
{
  "ref": "my-site",
  "name": "my-site-monitor",
  "schedule": "0 */6 * * *",
  "subject": "[my-site] {{count}} new item(s)",
  "template": "./templates/my-site",
  "email": "user@example.com"
}
```

- `ref` — name of the definition in `defs`
- `name` — unique name, used as state filename
- `schedule` — cron expression (e.g. `"0 8 * * *"` = daily 8am, `"0 */4 * * *"` = every 4h)
- `subject` — Liquid template for email subject (`{{count}}` = number of new items)
- `template` — path to template file relative to `~/.mutimon/`
- `email` — recipient email address
- `params` — values for URL template variables (e.g. `{"query": "python"}`)

## Template file

Create a plain text file in `~/.mutimon/templates/`. Example:

```
New items from My Site
Checked at: {{ now }}

Found {{ count }} new item(s)
============================================================
{% for item in items %}

{{ item.index }}. {{ item.title }}
     URL: {{ item.url }}
{% endfor %}

============================================================
```

Available variables: `{{ count }}`, `{{ now }}`, `{{ search_url }}`, and all extracted variables via `{{ item.varname }}`.
For list variables (from `collect: true`): `{% for tag in item.tags %}{{ tag }}{% unless forloop.last %}, {% endunless %}{% endfor %}`

## Complete example: Hacker News

Definition:
```json
"hackernews": {
  "url": "https://news.ycombinator.com",
  "query": {
    "type": "list",
    "selector": "tr.athing.submission",
    "id": { "type": "attribute", "name": "id" },
    "variables": {
      "title": {
        "selector": ".titleline > a",
        "value": { "type": "text" }
      },
      "url": {
        "selector": ".titleline > a",
        "value": { "type": "attribute", "name": "href" }
      },
      "score": {
        "sibling": true,
        "selector": ".score",
        "value": { "type": "text", "regex": "(\\d+)", "parse": "number" },
        "default": "0"
      }
    }
  }
}
```

Rule:
```json
{
  "ref": "hackernews",
  "name": "hackernews",
  "schedule": "0 */6 * * *",
  "subject": "Hacker News: {{count}} new stories",
  "template": "./templates/hackernews",
  "email": "user@example.com"
}
```

## State-machine tracking (`track`)

Use `track` instead of `validator` when you need per-threshold notifications (e.g. "notify each time the price crosses 190, then again at 200"). Mutually exclusive with `validator`.

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

- States evaluated top-down, first match wins
- Notifies when state index changes (unless new state is `silent`)
- `silent: true` saves state without notifying (use for "below all thresholds" reset state)
- Template vars: `{{ item._state_name }}`, `{{ item._prev_state_name }}`, `{{ item._value }}`

## Health checks (no `query`)

Definitions without a `query` section act as health checks. Mutimon makes an HTTP request and returns a single item with response metadata instead of parsing content.

```json
"health": {
  "url": "{{ url }}"
}
```

The returned item has an `http` object with: `code` (int, 0 on connection error), `method` (string), `body` (string), `headers` (dict, lowercase keys), `response_time` (float, seconds), `error` (string or null).

Use with `track` for up/down notifications:

```json
{
  "ref": "health",
  "name": "my-health",
  "schedule": "*/15 * * * *",
  "input": [
    { "params": { "url": "https://example.com/" } },
    { "params": { "url": "https://api.example.com/" } }
  ],
  "track": [
    { "name": "down", "test": "({{ http.code }} >= 400) | ({{ http.code }} == 0)" },
    { "name": "up", "test": "{{ http.code }} >= 200", "silent": true }
  ],
  "subject": "Site down",
  "template": "./templates/health",
  "email": "user@example.com"
}
```

Use `match` with `value` for header checks:
```json
{ "match": { "value": "{{ http.headers['content-type'] }}", "regex": "^application/json" } }
```

## Tips

- Always add `"expect"` selectors to detect page redesigns
- Use `"default": ""` on optional variables to prevent extraction errors
- Use `mon --dry-run --force <name>` to test without sending emails or saving state
- For RSS/Atom feeds: use `"format": "xml"` and element names as selectors (e.g. `"item"`, `"entry"`)
- The `id` field is crucial — it's how Mutimon tracks which items are new
- Use `track` instead of `validator` when you need per-threshold notifications (stock prices, scores, etc.)
