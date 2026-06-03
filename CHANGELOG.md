## 0.5.0
* expose `http` data as variable

## 0.4.0
* add `flatten` prop to control processing multiple pages in one rule
* add `parse: "url"` for URL normalization with `urljoin`
* add `enabled: false` option to disable a rule
* add per-rule debug logging (`log: true`)
* add `mon --init` to seed state without sending notifications
* don't swallow errors about undefined variables
* fix duplicate notifications from websites with unstable results
* add shell completion
* allow `--force` with multiple arguments

## 0.3.0
* add a way to interact with an API
* support authentication (cookie and token based)
* add tracking state machine (support price changes)
* allow an array of rules in a custom validator
* extend input syntax to allow using an array of data
* add searching and transformation of the HTML during extraction
* add `html2text` filter
* `mon --ai-guide` now prints the contents instead of path to the file

## 0.2.0
* add `--cron` option and improve cron setup

## 0.1.0
* First release
