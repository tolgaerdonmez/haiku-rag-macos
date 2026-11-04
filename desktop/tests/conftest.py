from pathlib import Path

import pytest

CONFIG = """\
environment: production
databases_include: {fragments}
hooks:
  - name: macos-folders
    options:
      directory: {fragments}
storage:
  data_dir: {app}
lancedb:
  databases:
    files: {app}/files.lancedb
embeddings:
  model:
    provider: openai
    name: test
    base_url: http://127.0.0.1:9/v1
    vector_dim: 8
ingester:
  queue:
    path: {app}/ingester.db
  api:
    host: 127.0.0.1
    port: 9
{extra}"""


def write_config(app: Path, extra: str = "") -> Path:
    app.mkdir(parents=True, exist_ok=True)
    path = app / "haiku.rag.yaml"
    path.write_text(
        CONFIG.format(app=app, fragments=app / "databases.d", extra=extra), encoding="utf-8"
    )
    return path


@pytest.fixture
def app(tmp_path: Path) -> Path:
    return tmp_path / "app"


@pytest.fixture
def config(app: Path, monkeypatch) -> Path:
    path = write_config(app)
    monkeypatch.setenv("HAIKU_RAG_CONFIG_PATH", str(path))
    return path


@pytest.fixture
def folder(tmp_path: Path):
    def make(name: str) -> Path:
        path = tmp_path / "docs" / name
        path.mkdir(parents=True)
        return path

    return make
