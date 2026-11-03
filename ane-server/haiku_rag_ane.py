"""An OpenAI-compatible embedding server running a Core ML model on the Neural Engine.

    haiku-rag-ane-server --model DIR [--host 127.0.0.1] [--port 58741] [--idle-exit 300]

DIR holds `model.mlpackage` and `tokenizer.json`, such as
tolgaerdonmez/jina-embeddings-v5-omni-nano-coreml from Hugging Face. The model
has one function per sequence length; each text runs on the shortest that fits
it, and a text longer than the longest is truncated to it. The model is loaded
with the CPU and Neural Engine compute units on the first request.

Endpoints:

    POST /v1/embeddings   {"input": str | [str], "model": ..., "encoding_format":
                          "float" | "base64", "dimensions": N}
    GET  /v1/models
    GET  /health

Started by launchd, it serves the socket launchd listens on (the plist's
"Listeners" entry), so the first request starts it; otherwise it binds HOST:PORT.
After --idle-exit seconds without a request it releases the model and exits, and
launchd starts it again on the next connection. 0 never exits.

It needs PyObjC's Core ML bindings, tokenizers and numpy: no coremltools, no torch.
"""

import argparse
import base64
import ctypes
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

MODEL_FILE = "model.mlpackage"
TOKENIZER_FILE = "tokenizer.json"
INPUTS = {"input_ids", "attention_mask"}
OUTPUT = "embedding"
DEFAULT_PORT = 58741
DEFAULT_NAME = "jina-embeddings-v5-omni-nano"
MAX_BODY = 64 * 1024 * 1024


def log(message: str) -> None:
    print(f"haiku-rag-ane-server: {message}", file=sys.stderr, flush=True)


# --- the model ----------------------------------------------------------------


class Session:
    """A compiled Core ML model, its per-length functions, and the tokenizer."""

    def __init__(self, model_dir: Path):
        import CoreML  # ty: ignore[unresolved-import]
        import Foundation  # ty: ignore[unresolved-import]
        import objc  # ty: ignore[unresolved-import]
        from tokenizers import Tokenizer

        # PyObjC creates the framework attributes at runtime.
        self._coreml: Any = CoreML
        self._foundation: Any = Foundation
        self._objc: Any = objc
        self._dtypes = {
            CoreML.MLMultiArrayDataTypeFloat16: np.float16,
            CoreML.MLMultiArrayDataTypeFloat32: np.float32,
            CoreML.MLMultiArrayDataTypeDouble: np.float64,
        }
        self._models: dict[int, Any] = {}
        package = model_dir / MODEL_FILE
        self._compiled, self._temporary = compiled_path(
            package, (package / "Manifest.json").stat().st_mtime, self._compile
        )
        try:
            self.functions, self.vector_dim = model_functions(self._describe())
        except BaseException:
            self.close()
            raise
        self._tokenizer = Tokenizer.from_file(str(model_dir / TOKENIZER_FILE))
        self._tokenizer.no_padding()
        self._tokenizer.enable_truncation(self.functions[-1][1])

    def _url(self, path: str | Path) -> Any:
        return self._foundation.NSURL.fileURLWithPath_(str(path))

    def _compile(self, package: Path) -> str:
        url = _checked(
            self._coreml.MLModel.compileModelAtURL_error_(self._url(package), None)
        )
        return str(url.path())

    def _describe(self) -> list[tuple[str | None, Any]]:
        """Each function's name and model description."""
        asset = _checked(
            self._coreml.MLModelAsset.modelAssetWithURL_error_(
                self._url(self._compiled), None
            )
        )
        names = _completed(asset.functionNamesWithCompletionHandler_)
        if not names:
            return [(None, _completed(asset.modelDescriptionWithCompletionHandler_))]
        describe = asset.modelDescriptionOfFunctionNamed_completionHandler_
        return [(str(name), _completed(partial(describe, name))) for name in names]

    def _model(self, function: str | None, length: int) -> Any:
        if length not in self._models:
            config = self._coreml.MLModelConfiguration.alloc().init()
            config.setComputeUnits_(self._coreml.MLComputeUnitsCPUAndNeuralEngine)
            if function is not None:
                config.setFunctionName_(function)
            self._models[length] = _checked(
                self._coreml.MLModel.modelWithContentsOfURL_configuration_error_(
                    self._url(self._compiled), config, None
                )
            )
        return self._models[length]

    def _input(self, values: list[int], length: int) -> Any:
        array = _checked(
            self._coreml.MLMultiArray.alloc().initWithShape_dataType_error_(
                [1, length], self._coreml.MLMultiArrayDataTypeInt32, None
            )
        )
        data = np.frombuffer(array.dataPointer().as_buffer(length * 4), np.int32)
        data[:] = 0
        data[: len(values)] = values
        return array

    def _output(self, prediction: Any) -> np.ndarray:
        value = prediction.featureValueForName_(OUTPUT).multiArrayValue()
        dtype = np.dtype(self._dtypes[value.dataType()])
        step = int(value.strides()[-1])
        size = ((self.vector_dim - 1) * step + 1) * dtype.itemsize
        data = np.frombuffer(value.dataPointer().as_buffer(size), dtype)[::step]
        return data.astype(np.float32)

    def count_tokens(self, texts: list[str]) -> int:
        return sum(len(e.ids) for e in self._tokenizer.encode_batch(texts))

    def encode(self, texts: list[str]) -> tuple[list[np.ndarray], int]:
        """The texts' vectors, and how many tokens they took."""
        vectors = []
        tokens = 0
        with self._objc.autorelease_pool():
            for encoding in self._tokenizer.encode_batch(texts):
                ids = encoding.ids
                tokens += len(ids)
                function, length = next(f for f in self.functions if f[1] >= len(ids))
                features = _checked(
                    self._coreml.MLDictionaryFeatureProvider.alloc().initWithDictionary_error_(
                        {
                            "input_ids": self._input(ids, length),
                            "attention_mask": self._input([1] * len(ids), length),
                        },
                        None,
                    )
                )
                prediction = _checked(
                    self._model(function, length).predictionFromFeatures_error_(
                        features, None
                    )
                )
                vectors.append(self._output(prediction))
        return vectors, tokens

    def close(self) -> None:
        self._models = {}
        if self._temporary:
            shutil.rmtree(self._compiled, ignore_errors=True)


def _checked(result: tuple[Any, Any]) -> Any:
    """The value of an Objective-C `...error:` call, raising its NSError."""
    value, error = result
    if error is not None:
        raise RuntimeError(f"Core ML: {error.localizedDescription()}")
    return value


def _completed(call: Callable[[Callable[..., None]], Any]) -> Any:
    """Run an Objective-C call taking a completion handler and wait for it."""
    done = threading.Event()
    result: list[Any] = []

    def handler(value: Any, error: Any) -> None:
        result.append((value, error))
        done.set()

    call(handler)
    done.wait()
    return _checked(result[0])


def compiled_path(
    package: Path, changed: float, compile_model: Callable[[Path], str]
) -> tuple[str, bool]:
    """Compile `package` to `model.mlmodelc` beside it, reused while newer than `changed`.

    Returns the compiled path and whether it is temporary. Core ML loads a
    compiled model at a stable path from its cache, so a read-only directory,
    which keeps the temporary compile, loads slower.
    """
    target = package.with_suffix(".mlmodelc")
    if target.exists() and target.stat().st_mtime >= changed:
        return str(target), False
    built = compile_model(package)
    try:
        staging = tempfile.mkdtemp(prefix=".haiku-rag-", dir=package.parent)
    except OSError:
        return built, True
    try:
        staged = shutil.move(built, Path(staging) / target.name)
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staged, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return str(target), False


def model_functions(
    descriptions: Sequence[tuple[str | None, Any]],
) -> tuple[list[tuple[str | None, int]], int]:
    """The model's functions with their sequence lengths, shortest first, and the
    vector dimension. Every function must take `input_ids` and `attention_mask` and
    return `embedding`, all of the same dimension."""
    functions = []
    dims = set()
    for name, description in descriptions:
        label = f"function {name}" if name else "the model"
        inputs = dict(description.inputDescriptionsByName())
        if set(inputs) != INPUTS:
            raise ValueError(
                f"Core ML {label} takes {sorted(inputs)}, expected {sorted(INPUTS)}."
            )
        output = dict(description.outputDescriptionsByName()).get(OUTPUT)
        if output is None:
            raise ValueError(f"Core ML {label} has no '{OUTPUT}' output.")
        shape = list(output.multiArrayConstraint().shape())
        if not shape:
            raise ValueError(f"Core ML {label} gives '{OUTPUT}' no shape.")
        dims.add(int(shape[-1]))
        length = inputs["input_ids"].multiArrayConstraint().shape()[-1]
        functions.append((name, int(length)))
    if len(dims) != 1:
        raise ValueError(f"Core ML functions return different dimensions: {sorted(dims)}.")
    return sorted(functions, key=lambda f: f[1]), dims.pop()


class Encoder:
    """The model, loaded on first use and released when idle; one text at a time."""

    def __init__(self, model_dir: Path, session: Callable[[Path], Any] = Session):
        for file in (MODEL_FILE, TOKENIZER_FILE):
            if not (model_dir / file).exists():
                raise FileNotFoundError(
                    f"{model_dir / file} not found: the model directory holds "
                    f"{MODEL_FILE} and {TOKENIZER_FILE}."
                )
        self._dir = model_dir
        self._new_session = session
        self._session: Any = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def _loaded(self) -> Any:
        if self._session is None:
            start = time.monotonic()
            self._session = self._new_session(self._dir)
            log(f"model loaded in {time.monotonic() - start:.2f} s from {self._dir}")
        return self._session

    def encode(self, texts: list[str]) -> tuple[list[np.ndarray], int]:
        with self._lock:
            return self._loaded().encode(texts)

    def release(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None
                log("model released")


# --- the API ------------------------------------------------------------------


class RequestError(ValueError):
    pass


def parse_request(body: bytes) -> tuple[list[str], str, int | None]:
    """The texts, the encoding format and the dimensions of an embeddings request."""
    try:
        request = json.loads(body)
    except ValueError as exc:
        raise RequestError(f"the body is not JSON: {exc}") from None
    if not isinstance(request, dict):
        raise RequestError("the body is not a JSON object")
    texts = request.get("input")
    if isinstance(texts, str):
        texts = [texts]
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        raise RequestError("'input' must be a string or a list of strings")
    if not texts:
        raise RequestError("'input' is empty")
    encoding = request.get("encoding_format") or "float"
    if encoding not in ("float", "base64"):
        raise RequestError("'encoding_format' must be 'float' or 'base64'")
    dimensions = request.get("dimensions")
    if dimensions is not None and (
        not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 1
    ):
        raise RequestError("'dimensions' must be a positive integer")
    return texts, encoding, dimensions


def embeddings_response(
    vectors: list[np.ndarray], tokens: int, name: str, encoding: str, dimensions: int | None
) -> dict:
    """The OpenAI response. The vectors are returned as the model gives them (it
    normalises them itself); one cut to `dimensions` (Matryoshka) is normalised again."""
    data = []
    for i, vector in enumerate(vectors):
        if dimensions and dimensions < len(vector):
            vector = vector[:dimensions]
            vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
        vector = vector.astype("<f4")
        if encoding == "base64":
            embedding: Any = base64.b64encode(vector.tobytes()).decode("ascii")
        else:
            embedding = vector.tolist()
        data.append({"object": "embedding", "index": i, "embedding": embedding})
    return {
        "object": "list",
        "data": data,
        "model": name,
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, listener: socket.socket, encoder: Encoder, name: str, idle_exit: float):
        super().__init__(listener.getsockname()[:2], Handler, bind_and_activate=False)
        self.socket.close()
        self.socket = listener
        self.encoder = encoder
        self.name = name
        self.idle_exit = idle_exit
        self.busy = 0
        self.last_used = time.monotonic()
        self.activity = threading.Lock()

    def begin(self) -> None:
        with self.activity:
            self.busy += 1
            self.last_used = time.monotonic()

    def end(self) -> None:
        with self.activity:
            self.busy -= 1
            self.last_used = time.monotonic()

    def idle_for(self) -> float:
        """Seconds without a request, 0 while one runs."""
        with self.activity:
            return 0.0 if self.busy else time.monotonic() - self.last_used


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Server

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"error": {"message": message, "type": "invalid_request_error"}})

    def do_GET(self) -> None:
        self.server.begin()
        try:
            path = self.path.split("?", 1)[0].rstrip("/")
            if path == "/v1/models":
                model = {"id": self.server.name, "object": "model", "owned_by": "local"}
                self._send(HTTPStatus.OK, {"object": "list", "data": [model]})
            elif path == "/health":
                self._send(
                    HTTPStatus.OK,
                    {
                        "model": self.server.name,
                        "loaded": self.server.encoder.loaded,
                        "compute_units": "CPU_AND_NE",
                        "idle_exit_s": self.server.idle_exit,
                    },
                )
            else:
                self._error(HTTPStatus.NOT_FOUND, f"no {path}")
        finally:
            self.server.end()

    def do_POST(self) -> None:
        self.server.begin()
        try:
            path = self.path.split("?", 1)[0].rstrip("/")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.close_connection = True
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the body is too large")
                return
            body = self.rfile.read(length)
            if path != "/v1/embeddings":
                self._error(HTTPStatus.NOT_FOUND, f"no {path}")
                return
            try:
                texts, encoding, dimensions = parse_request(body)
            except RequestError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            try:
                vectors, tokens = self.server.encoder.encode(texts)
            except Exception as exc:  # noqa: BLE001 - reported to the client and the log
                log(f"embedding failed: {exc!r}")
                self._send(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": {"message": str(exc), "type": "server_error"}},
                )
                return
            self._send(
                HTTPStatus.OK,
                embeddings_response(vectors, tokens, self.server.name, encoding, dimensions),
            )
        finally:
            self.server.end()


# --- running ------------------------------------------------------------------


def launchd_socket(name: str = "Listeners") -> socket.socket | None:
    """The listening socket launchd holds for this job, or None outside launchd."""
    if sys.platform != "darwin":
        return None
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    activate = libc.launch_activate_socket
    activate.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_int)),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    activate.restype = ctypes.c_int
    fds = ctypes.POINTER(ctypes.c_int)()
    count = ctypes.c_size_t(0)
    if activate(name.encode(), ctypes.byref(fds), ctypes.byref(count)) != 0 or not count.value:
        return None
    descriptors = [fds[i] for i in range(count.value)]
    libc.free(fds)
    for fd in descriptors[1:]:
        os.close(fd)
    listener = socket.socket(fileno=descriptors[0])
    # On macOS an accepted socket inherits the listener's O_NONBLOCK.
    listener.setblocking(True)
    return listener


def bound_socket(host: str, port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(64)
    return listener


def watch_idle(server: Server, poll: float = 1.0) -> None:
    """Shuts the server down once it has been idle for its idle_exit seconds."""
    while True:
        time.sleep(poll)
        if server.idle_for() >= server.idle_exit:
            log(f"idle for {server.idle_exit:g} s: releasing the model and exiting")
            server.shutdown()
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="haiku-rag-ane-server", description=__doc__.split("\n")[0])
    parser.add_argument("--model", required=True, type=Path, help="directory with model.mlpackage and tokenizer.json")
    parser.add_argument("--name", default=DEFAULT_NAME, help="the model id it reports")
    parser.add_argument("--host", default="127.0.0.1", help="address to bind outside launchd")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to bind outside launchd")
    parser.add_argument("--idle-exit", type=float, default=300.0, help="seconds without a request before exiting; 0 never")
    args = parser.parse_args(argv)

    try:
        encoder = Encoder(args.model.expanduser())
    except FileNotFoundError as exc:
        log(str(exc))
        return 66
    listener = launchd_socket()
    source = "launchd's socket"
    if listener is None:
        listener = bound_socket(args.host, args.port)
        source = "its own socket"
    server = Server(listener, encoder, args.name, args.idle_exit)
    host, port = listener.getsockname()[:2]
    log(f"serving {args.name} on http://{host}:{port}/v1 ({source})")
    if args.idle_exit > 0:
        threading.Thread(target=watch_idle, args=(server,), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        encoder.release()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
