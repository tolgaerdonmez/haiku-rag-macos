"""The Neural Engine embedding server. Run with its environment:

    uv run --project ane-server pytest tests
"""

import base64
import importlib.util
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[1]
INSTALLED_MODEL = (
    Path.home() / "Library" / "Application Support" / "haiku.rag" / "models" / "jina-nano-coreml"
)


def server_module():
    spec = importlib.util.spec_from_file_location(
        "haiku_rag_ane", ROOT / "ane-server" / "haiku_rag_ane.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["haiku_rag_ane"] = module
    spec.loader.exec_module(module)
    return module


ane = server_module()


class FakeSession:
    """Vectors from the text length, so a test can tell texts apart."""

    opened = 0

    def __init__(self, model_dir: Path):
        FakeSession.opened += 1
        self.closed = False

    def encode(self, texts):
        vectors = []
        for text in texts:
            v = np.zeros(4, dtype=np.float32)
            v[len(text) % 4] = 1.0
            vectors.append(v)
        return vectors, sum(len(t.split()) for t in texts)

    def close(self):
        self.closed = True


def model_dir(tmp_path: Path) -> Path:
    (tmp_path / "model.mlpackage").mkdir()
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    return tmp_path


def serve(tmp_path: Path, idle_exit: float = 0):
    encoder = ane.Encoder(model_dir(tmp_path), session=FakeSession)
    server = ane.Server(ane.bound_socket("127.0.0.1", 0), encoder, "test-model", idle_exit)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.socket.getsockname()[1]}"


def post(url: str, payload) -> tuple[int, dict]:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def test_request_parsing_takes_a_string_or_a_list():
    assert ane.parse_request(b'{"input": "a"}') == (["a"], "float", None)
    assert ane.parse_request(
        b'{"input": ["a", "b"], "encoding_format": "base64", "dimensions": 256}'
    ) == (["a", "b"], "base64", 256)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"input": []}',
        b'{"input": [1, 2]}',
        b'{"input": "a", "encoding_format": "int8"}',
        b'{"input": "a", "dimensions": 0}',
        b'{"input": "a", "dimensions": true}',
    ],
)
def test_bad_requests_are_refused(body):
    with pytest.raises(ane.RequestError):
        ane.parse_request(body)


def test_vectors_are_returned_as_the_model_gives_them():
    vector = np.array([0.6, 0.8, 0.0001], dtype=np.float32)
    response = ane.embeddings_response([vector], 3, "m", "float", None)
    assert response["data"][0]["embedding"] == vector.tolist()
    assert response["usage"] == {"prompt_tokens": 3, "total_tokens": 3}
    encoded = ane.embeddings_response([vector], 3, "m", "base64", None)
    raw = base64.b64decode(encoded["data"][0]["embedding"])
    assert np.frombuffer(raw, "<f4").tolist() == vector.tolist()


def test_dimensions_cut_and_normalise_again():
    vector = np.array([3.0, 4.0, 12.0], dtype=np.float32)
    response = ane.embeddings_response([vector], 1, "m", "float", 2)
    assert response["data"][0]["embedding"] == pytest.approx([0.6, 0.8])


def description(inputs: dict, output_dim: int):
    def constraint(shape):
        return SimpleNamespace(multiArrayConstraint=lambda: SimpleNamespace(shape=lambda: shape))

    return SimpleNamespace(
        inputDescriptionsByName=lambda: {k: constraint(v) for k, v in inputs.items()},
        outputDescriptionsByName=lambda: {"embedding": constraint([1, output_dim])},
    )


def test_model_functions_are_sorted_by_length():
    ok = {"input_ids": [1, 0], "attention_mask": [1, 0]}
    functions, dim = ane.model_functions(
        [
            ("L512", description({**ok, "input_ids": [1, 512]}, 768)),
            ("L64", description({**ok, "input_ids": [1, 64]}, 768)),
        ]
    )
    assert functions == [("L64", 64), ("L512", 512)]
    assert dim == 768


def test_model_functions_refuse_other_inputs_or_mixed_dimensions():
    with pytest.raises(ValueError, match="takes"):
        ane.model_functions([("f", description({"input_ids": [1, 8]}, 8))])
    ok = {"input_ids": [1, 8], "attention_mask": [1, 8]}
    with pytest.raises(ValueError, match="different dimensions"):
        ane.model_functions([("a", description(ok, 8)), ("b", description(ok, 16))])


def test_a_compiled_model_is_reused_while_it_is_newer(tmp_path):
    package = tmp_path / "model.mlpackage"
    package.mkdir()
    compiled = []

    def compile_model(path):
        out = tmp_path / f"build{len(compiled)}" / "model.mlmodelc"
        out.mkdir(parents=True)
        compiled.append(out)
        return str(out)

    path, temporary = ane.compiled_path(package, time.time() - 10, compile_model)
    assert path == str(tmp_path / "model.mlmodelc") and not temporary
    ane.compiled_path(package, time.time() - 10, compile_model)
    assert len(compiled) == 1
    ane.compiled_path(package, time.time() + 10, compile_model)
    assert len(compiled) == 2


def test_a_model_directory_without_the_model_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        ane.Encoder(tmp_path)


def test_the_server_answers_the_openai_api_and_loads_the_model_once(tmp_path):
    FakeSession.opened = 0
    server, thread, url = serve(tmp_path)
    try:
        health = get(f"{url}/health")
        assert health["loaded"] is False and health["compute_units"] == "CPU_AND_NE"
        assert get(f"{url}/v1/models")["data"][0]["id"] == "test-model"
        status, body = post(f"{url}/v1/embeddings", {"input": ["ab", "abc"], "model": "x"})
        assert status == 200
        assert [d["index"] for d in body["data"]] == [0, 1]
        assert body["data"][0]["embedding"] == [0.0, 0.0, 1.0, 0.0]
        assert body["model"] == "test-model"
        post(f"{url}/v1/embeddings", {"input": "again"})
        assert FakeSession.opened == 1
        assert get(f"{url}/health")["loaded"] is True
        status, body = post(f"{url}/v1/embeddings", {"input": []})
        assert status == 400 and "empty" in body["error"]["message"]
        status, _ = post(f"{url}/v1/chat/completions", {"input": "a"})
        assert status == 404
    finally:
        server.shutdown()
        thread.join()


def test_the_server_exits_once_idle(tmp_path):
    server, thread, url = serve(tmp_path, idle_exit=0.5)
    post(f"{url}/v1/embeddings", {"input": "a"})
    watcher = threading.Thread(target=ane.watch_idle, args=(server, 0.05))
    watcher.start()
    watcher.join(timeout=5)
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_outside_launchd_there_is_no_socket_to_take():
    assert ane.launchd_socket() is None


@pytest.mark.skipif(
    sys.platform != "darwin" or not (INSTALLED_MODEL / "model.mlpackage").exists(),
    reason="needs the installed Core ML model",
)
def test_the_installed_model_embeds_on_the_neural_engine():
    session = ane.Session(INSTALLED_MODEL)
    try:
        assert session.vector_dim == 768
        assert [f[1] for f in session.functions] == sorted(f[1] for f in session.functions)
        vectors, tokens = session.encode(["Query: chain rule", "Document: " + "word " * 600])
        assert tokens > 600
        for vector in vectors:
            assert vector.shape == (768,)
            assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=2e-3)
    finally:
        session.close()
