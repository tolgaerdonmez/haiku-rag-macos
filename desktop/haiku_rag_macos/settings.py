"""Where the desktop parts find haiku.rag's configuration, and what they read
from it.

Everything here comes from the one haiku.rag configuration file the service
runs with (HAIKU_RAG_CONFIG_PATH): the databases, the directory of database
fragments the folders live in (`databases_include`) and the ingester's API.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from haiku.rag.config import AppConfig

DEFAULT_CONFIG = Path.home() / "Library" / "Application Support" / "haiku.rag" / "haiku.rag.yaml"


class SettingsError(ValueError):
    """The configuration cannot serve the desktop parts."""


def config_path(given: str | os.PathLike[str] | None = None) -> Path:
    """The configuration file: `given`, else HAIKU_RAG_CONFIG_PATH, else the
    installed one."""
    if given is not None:
        return Path(given).expanduser()
    if env := os.environ.get("HAIKU_RAG_CONFIG_PATH"):
        return Path(env).expanduser()
    return DEFAULT_CONFIG


def _raw(path: Path) -> dict[str, Any]:
    """The main file as haiku.rag reads it, environment references expanded,
    before `databases_include` is merged and removed."""
    from haiku.rag.config import expand_env_vars

    config_dir = path.resolve().parent
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return expand_env_vars(data, {"HAIKU_RAG_CONFIG_DIR": str(config_dir)})


@dataclass(frozen=True)
class Settings:
    """What the desktop parts need from the configuration at `path`."""

    path: Path
    config: "AppConfig"
    fragments: Path

    @classmethod
    def load(cls, given: str | os.PathLike[str] | None = None) -> "Settings":
        from haiku.rag.config import AppConfig, load_yaml_config

        path = config_path(given)
        if not path.is_file():
            raise SettingsError(f"{path}: no such configuration file")
        include = _raw(path).get("databases_include")
        if not include:
            raise SettingsError(
                f"{path} sets no databases_include: the folders live in the "
                "database fragments that directory holds"
            )
        fragments = path.resolve().parent / str(include)
        return cls(path, AppConfig.model_validate(load_yaml_config(path)), fragments)

    def reload(self) -> "Settings":
        return Settings.load(self.path)

    @property
    def databases(self) -> list[str]:
        """The configured databases, by name."""
        from haiku.rag.config.models import DEFAULT_DATABASE_NAME

        return list(self.config.lancedb.databases) or [DEFAULT_DATABASE_NAME]

    @property
    def ingester_url(self) -> str:
        api = self.config.ingester.api
        host = "127.0.0.1" if api.host in ("0.0.0.0", "") else api.host
        return f"http://{host}:{api.port}{api.root_path}"

    @property
    def auth_token(self) -> str | None:
        return self.config.ingester.api.auth_token
