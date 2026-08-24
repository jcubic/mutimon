"""End-to-end CLI tests for mutimon."""

import json
import os
import subprocess
import sys
import tempfile



_TEST_HOME = tempfile.mkdtemp(prefix="mutimon-test-")
_TEST_ENV = {**os.environ, "HOME": _TEST_HOME}


def run_mon(*args, input_text=None):
    """Run mon command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "mutimon.main"] + list(args),
        capture_output=True,
        text=True,
        input=input_text,
        timeout=30,
        env=_TEST_ENV,
    )
    return result.returncode, result.stdout, result.stderr


class TestHelp:
    def test_help_flag(self):
        rc, out, _ = run_mon("--help")
        assert rc == 0
        assert "Mutimon" in out
        assert "--dry-run" in out
        assert "--force" in out
        assert "--validate" in out
        assert "--ai-guide" in out
        assert "--list" in out

    def test_help_shows_ai_guide_tip(self):
        rc, out, _ = run_mon("--help")
        assert "claude" in out.lower() or "AI" in out


class TestFirstRun:
    def setup_method(self):
        """Clean up the test mutimon directory."""
        import shutil

        mutimon_dir = os.path.join(_TEST_HOME, ".mutimon")
        if os.path.exists(mutimon_dir):
            shutil.rmtree(mutimon_dir)

    def test_creates_skeleton(self):
        rc, out, _ = run_mon()
        assert "Creating skeleton" in out
        config_path = os.path.join(_TEST_HOME, ".mutimon", "config.json")
        assert os.path.exists(config_path)

    def test_skeleton_config_shows_guide(self):
        # First run creates skeleton
        run_mon()
        # Second run detects unchanged config
        rc, out, _ = run_mon()
        assert "Quick setup" in out
        assert "TIP" in out
        assert "claude" in out.lower() or "AI" in out


class TestValidate:
    def setup_method(self):
        import shutil

        self.mutimon_dir = os.path.join(_TEST_HOME, ".mutimon")
        if os.path.exists(self.mutimon_dir):
            shutil.rmtree(self.mutimon_dir)
        os.makedirs(os.path.join(self.mutimon_dir, "data"), exist_ok=True)
        os.makedirs(os.path.join(self.mutimon_dir, "templates"), exist_ok=True)

    def _write_config(self, config):
        config_path = os.path.join(self.mutimon_dir, "config.json")
        with open(config_path, "w") as f:
            json.dump(config, f)

    def test_valid_config(self):
        self._write_config(
            {
                "email": {
                    "server": {
                        "host": "smtp.real.com",
                        "port": 587,
                        "password": "real",
                        "email": "me@real.com",
                    }
                },
                "defs": {},
                "rules": [],
            }
        )
        rc, out, _ = run_mon("--validate")
        assert rc == 0
        assert "valid" in out.lower()

    def test_invalid_config(self):
        self._write_config({"bad": "config"})
        rc, _, err = run_mon("--validate")
        assert rc != 0

    def test_skeleton_email_rejected(self):
        self._write_config(
            {
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
        )
        rc, _, err = run_mon("--validate")
        assert rc == 1
        assert "placeholder" in err.lower() or "default" in err.lower()


class TestAiGuide:
    def test_prints_contents(self):
        rc, out, _ = run_mon("--ai-guide")
        assert rc == 0
        assert "# Mutimon" in out
        assert "Definition structure" in out


class TestList:
    def setup_method(self):
        import shutil

        self.mutimon_dir = os.path.join(_TEST_HOME, ".mutimon")
        if os.path.exists(self.mutimon_dir):
            shutil.rmtree(self.mutimon_dir)
        os.makedirs(os.path.join(self.mutimon_dir, "data"), exist_ok=True)
        os.makedirs(os.path.join(self.mutimon_dir, "templates"), exist_ok=True)

    def _write_config(self, config):
        config_path = os.path.join(self.mutimon_dir, "config.json")
        with open(config_path, "w") as f:
            json.dump(config, f)

    def test_lists_rules(self):
        self._write_config(
            {
                "email": {
                    "server": {
                        "host": "smtp.real.com",
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
                            "selector": "div",
                            "variables": {},
                        },
                    }
                },
                "rules": [
                    {
                        "ref": "site",
                        "name": "rule-one",
                        "schedule": "0 * * * *",
                        "subject": "Test",
                        "template": "./templates/test",
                        "email": "x@x.com",
                    },
                    {
                        "ref": "site",
                        "name": "rule-two",
                        "schedule": "0 8 * * *",
                        "subject": "Test 2",
                        "template": "./templates/test",
                        "email": "x@x.com",
                    },
                ],
            }
        )
        rc, out, _ = run_mon("--list")
        assert rc == 0
        lines = out.strip().split("\n")
        assert "rule-one" in lines
        assert "rule-two" in lines

    def test_empty_rules(self):
        self._write_config(
            {
                "email": {
                    "server": {
                        "host": "smtp.real.com",
                        "port": 587,
                        "password": "x",
                        "email": "x@x.com",
                    }
                },
                "defs": {},
                "rules": [],
            }
        )
        rc, out, _ = run_mon("--list")
        assert rc == 0
        assert "No rules" in out


class TestDryRun:
    def setup_method(self):
        import shutil

        self.mutimon_dir = os.path.join(_TEST_HOME, ".mutimon")
        if os.path.exists(self.mutimon_dir):
            shutil.rmtree(self.mutimon_dir)
        os.makedirs(os.path.join(self.mutimon_dir, "data"), exist_ok=True)
        os.makedirs(os.path.join(self.mutimon_dir, "templates"), exist_ok=True)

    def _write_config(self, config):
        config_path = os.path.join(self.mutimon_dir, "config.json")
        with open(config_path, "w") as f:
            json.dump(config, f)

    def test_dry_run_does_not_crash(self):
        self._write_config(
            {
                "email": {
                    "server": {
                        "host": "smtp.real.com",
                        "port": 587,
                        "password": "x",
                        "email": "x@x.com",
                    }
                },
                "defs": {},
                "rules": [],
            }
        )
        rc, out, _ = run_mon("--dry-run", "--force")
        assert rc == 0


class TestReset:
    def setup_method(self):
        import shutil

        self.mutimon_dir = os.path.join(_TEST_HOME, ".mutimon")
        if os.path.exists(self.mutimon_dir):
            shutil.rmtree(self.mutimon_dir)
        self.data_dir = os.path.join(self.mutimon_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(os.path.join(self.mutimon_dir, "templates"), exist_ok=True)

    def _write_config(self, config):
        config_path = os.path.join(self.mutimon_dir, "config.json")
        with open(config_path, "w") as f:
            json.dump(config, f)

    def _base_config(self):
        return {
            "email": {
                "server": {
                    "host": "smtp.real.com",
                    "port": 587,
                    "password": "x",
                    "email": "x@x.com",
                }
            },
            "defs": {
                "site": {
                    "url": "https://example.com",
                    "query": {"type": "list", "selector": "div", "variables": {}},
                }
            },
            "rules": [
                {
                    "ref": "site",
                    "name": "my-rule",
                    "schedule": "0 * * * *",
                    "subject": "Test",
                    "template": "./templates/test",
                    "email": "x@x.com",
                }
            ],
        }

    def test_reset_in_help(self):
        rc, out, _ = run_mon("--help")
        assert rc == 0
        assert "--reset" in out

    def test_reset_clears_data(self):
        self._write_config(self._base_config())
        state_file = os.path.join(self.data_dir, "my-rule")
        run_file = os.path.join(self.data_dir, ".lastrun_my-rule")
        with open(state_file, "w") as f:
            f.write("[]")
        with open(run_file, "w") as f:
            f.write("2020-01-01T00:00:00")

        rc, out, _ = run_mon("--reset", "my-rule")
        assert rc == 0
        assert not os.path.exists(state_file)
        assert not os.path.exists(run_file)

    def test_reset_unknown_rule_errors(self):
        self._write_config(self._base_config())
        rc, _, err = run_mon("--reset", "nope")
        assert rc != 0
        assert "not found" in err.lower()

    def test_reset_requires_rule_name(self):
        self._write_config(self._base_config())
        rc, _, _ = run_mon("--reset")
        assert rc != 0
