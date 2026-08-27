"""Environment-driven settings.

Configuration bugs are deploy-time bugs: they surface in the one environment
you cannot attach a debugger to.  These tests pin the behaviour that makes them
surface earlier -- coercion at load, not at first use, and a production guard
that refuses to run without a secret.
"""

from __future__ import annotations

import dataclasses

import pytest

from slowapi.config import Settings, from_env, load_dotenv
from slowapi.exceptions import ConfigurationError


@dataclasses.dataclass
class AppConfig:
    database_url: str
    pool_size: int = 5
    debug: bool = False
    origins: list[str] = dataclasses.field(default_factory=list)


class TestFromEnv:
    def test_values_are_coerced_to_the_declared_types(self):
        config = from_env(
            AppConfig,
            environ={
                "DATABASE_URL": "postgres://localhost/x",
                "POOL_SIZE": "20",
                "DEBUG": "true",
                "ORIGINS": "a.com,b.com",
            },
        )

        assert config.pool_size == 20 and isinstance(config.pool_size, int)
        assert config.debug is True
        assert config.origins == ["a.com", "b.com"]

    def test_a_prefix_namespaces_the_variables(self):
        config = from_env(AppConfig, prefix="myapp_", environ={"MYAPP_DATABASE_URL": "x"})
        assert config.database_url == "x"

    def test_absent_variables_fall_back_to_the_declared_default(self):
        config = from_env(AppConfig, environ={"DATABASE_URL": "x"})
        assert config.pool_size == 5 and config.origins == []

    def test_a_required_field_with_no_value_names_itself(self):
        with pytest.raises(ConfigurationError, match="Missing required configuration"):
            from_env(AppConfig, environ={})

    def test_every_bad_value_is_reported_at_once(self):
        """Fixing one variable only to hit the next one is a wasted deploy."""
        with pytest.raises(ConfigurationError) as info:
            from_env(
                AppConfig,
                environ={"DATABASE_URL": "x", "POOL_SIZE": "many", "DEBUG": "perhaps"},
            )

        message = str(info.value)
        assert "POOL_SIZE" in message and "DEBUG" in message

    def test_a_non_dataclass_is_rejected(self):
        with pytest.raises(ConfigurationError, match="must be a dataclass"):
            from_env(dict)  # type: ignore[arg-type]


class TestDotenv:
    def test_pairs_are_loaded_and_quotes_stripped(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# a comment\n"
            "\n"
            "export DATABASE_URL='postgres://localhost/x'\n"
            'SECRET_KEY="s3cret"\n'
            "not a pair\n"
        )
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)

        loaded = load_dotenv(str(env_file))

        assert loaded == {"DATABASE_URL": "postgres://localhost/x", "SECRET_KEY": "s3cret"}

    def test_the_real_environment_wins_by_default(self, tmp_path, monkeypatch):
        """A stray .env baked into an image must not override the deployment."""
        env_file = tmp_path / ".env"
        env_file.write_text("DATABASE_URL=from-file\n")
        monkeypatch.setenv("DATABASE_URL", "from-environment")

        load_dotenv(str(env_file))

        import os

        assert os.environ["DATABASE_URL"] == "from-environment"

    def test_override_is_available_when_asked_for(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("DATABASE_URL=from-file\n")
        monkeypatch.setenv("DATABASE_URL", "from-environment")

        load_dotenv(str(env_file), override=True)

        import os

        assert os.environ["DATABASE_URL"] == "from-file"

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert load_dotenv(str(tmp_path / "nope.env")) == {}


class TestSettings:
    def test_production_is_recognised_by_either_spelling(self):
        assert Settings(environment="production").is_production
        assert Settings(environment="PROD").is_production
        assert not Settings(environment="staging").is_production

    def test_json_logs_and_docs_follow_the_environment_unless_set(self):
        development = Settings(environment="development")
        assert not development.use_json_logs and development.show_docs

        production = Settings(environment="production")
        assert production.use_json_logs and not production.show_docs

        # An explicit choice always beats the inference.
        assert Settings(environment="production", docs_enabled=True).show_docs
        assert not Settings(environment="production", log_json=False).use_json_logs

    def test_production_refuses_to_run_without_a_secret(self):
        with pytest.raises(ConfigurationError, match="python -m slowapi secret"):
            Settings(environment="production").require_secret()

    def test_development_gets_an_obviously_unsafe_placeholder(self):
        """Named so that nobody mistakes it for a real key in a log line."""
        assert "do-not-deploy" in Settings(environment="development").require_secret()

    def test_a_configured_secret_is_returned_as_is(self):
        assert Settings(secret_key="abc", environment="production").require_secret() == "abc"

    def test_load_reads_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APP_NAME", "billing")
        monkeypatch.setenv("PORT", "9000")
        settings = Settings.load(dotenv=None)
        assert settings.app_name == "billing" and settings.port == 9000


class TestSequenceFields:
    """The failure mode this guards: a list that silently becomes one string."""

    def test_a_comma_separated_variable_becomes_a_list(self):
        config = from_env(AppConfig, environ={"DATABASE_URL": "x", "ORIGINS": "a.com, b.com"})
        assert config.origins == ["a.com", "b.com"]

    def test_empty_entries_are_dropped(self):
        config = from_env(AppConfig, environ={"DATABASE_URL": "x", "ORIGINS": "a.com,,"})
        assert config.origins == ["a.com"]

    def test_a_single_value_is_still_a_list(self):
        config = from_env(AppConfig, environ={"DATABASE_URL": "x", "ORIGINS": "a.com"})
        assert config.origins == ["a.com"]

    def test_the_framework_settings_parse_their_own_lists(self):
        settings = from_env(Settings, environ={"CORS_ORIGINS": "https://a.com,https://b.com"})
        assert settings.cors_origins == ["https://a.com", "https://b.com"]
