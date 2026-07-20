import pytest

import env


class TestLoadEnv:
    def test_loads_key_value_lines_into_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TOAST_BASE_URL", raising=False)
        path = tmp_path / ".env"
        path.write_text(
            "# a comment\n"
            "\n"
            "TOAST_BASE_URL=https://ws-api.toasttab.com\n"
        )
        assert env.load_env(path)["TOAST_BASE_URL"] == "https://ws-api.toasttab.com"

    def test_the_real_environment_wins_over_the_file(self, tmp_path, monkeypatch):
        # How the GitHub Actions runner works: secrets arrive as environment
        # variables and nothing on disk may shadow them.
        monkeypatch.setenv("TOAST_BASE_URL", "https://from-secrets.example")
        path = tmp_path / ".env"
        path.write_text("TOAST_BASE_URL=https://from-file.example\n")
        assert env.load_env(path)["TOAST_BASE_URL"] == "https://from-secrets.example"

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        # A runner has no .env at all; only a missing *value* is a failure, and
        # that is the readers' job to report.
        env.load_env(tmp_path / "does-not-exist.env")


class TestResolve:
    def test_returns_the_given_mapping_untouched(self):
        environ = {"TOAST_BASE_URL": "https://given.example"}
        assert env.resolve(environ) is environ

    def test_falls_back_to_the_loaded_environment(self, monkeypatch):
        monkeypatch.setenv("TOAST_BASE_URL", "https://ambient.example")
        assert env.resolve()["TOAST_BASE_URL"] == "https://ambient.example"


class TestRequire:
    def test_returns_the_value(self):
        assert env.require("A", {"A": "value"}) == "value"

    def test_missing_raises_naming_the_variable(self):
        with pytest.raises(RuntimeError, match="MISSING_KEY is not set"):
            env.require("MISSING_KEY", {})

    def test_empty_value_is_treated_as_missing(self):
        with pytest.raises(RuntimeError, match="BLANK is not set"):
            env.require("BLANK", {"BLANK": ""})

    def test_raises_the_callers_error_type_with_its_hint(self):
        class CustomError(Exception):
            pass

        with pytest.raises(CustomError, match="MISSING is not set. See the docs."):
            env.require("MISSING", {}, CustomError, "See the docs.")
