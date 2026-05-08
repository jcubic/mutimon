## 0.3.0
* add a way to interact with an API
* support authentication (cookie and token based)
* add tracking state machine (support price changes)
* allow an array of rules in a custom validator
* extend input syntax to allow using an array of data
* add searching and transformation of the HTML during extraction
* add `html2text` filter
* `mon --ai-guide` now prints the contents instead of path to the file
* add `enabled: false` option to disable a rule
* add per-rule debug logging (`log: true`)
* add `mon --init` to seed state without sending notifications
* don't swallow errors about undefined variables
* fix duplicate notifications from websites with unstable results

## 0.2.0
* add `--cron` option and improve cron setup

## 0.1.0
* First release
