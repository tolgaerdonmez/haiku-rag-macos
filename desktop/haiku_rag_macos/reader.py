"""The search service the Raycast extension talks to, on 127.0.0.1:8765.

It reads, and haiku-ingester writes: searches open the databases read-only
through haiku.rag's client; folders are the register of database fragments
(folders.py), and a change asks the ingester to reload; status and failures
come from the ingester's API (`/stats`, `/sources`, `/dlq`). The HTTP contract
is docs/search-api.md.

launchd starts it on the first connection to its socket, which the launcher
hands on as HAIKU_RAG_LISTEN_FD, and it exits after `idle_exit_s` without a
request. Starting, it has launchd start the ingester too, so what changed
while both were stopped is indexed.
"""

import asyncio
import logging
import os
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from haiku.rag.store.exceptions import UnknownDatabaseError
from pydantic import BaseModel, Field

from haiku_rag_macos.folders import (
    Folder,
    FolderConflictError,
    FolderRegister,
    FolderRegisterError,
    UnknownFolderError,
)
from haiku_rag_macos.hits import SearchResponse, path_from_uri
from haiku_rag_macos.ingester_api import IngesterAPI, IngesterError, IngesterUnavailable
from haiku_rag_macos.search import SearchService
from haiku_rag_macos.settings import Settings

logger = logging.getLogger(__name__)

_UNREACHABLE = (IngesterUnavailable, IngesterError, TimeoutError)


class FolderStatus(BaseModel):
    watching: bool
    documents: int
    queued: int
    failed: int
    last_scan_at: datetime | None = None


class FolderOut(BaseModel):
    id: str
    database: str
    path: str
    include: list[str]
    exclude: list[str]
    status: FolderStatus | None = None


class NewFolder(BaseModel):
    path: str
    id: str | None = None
    database: str | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class FolderPatch(BaseModel):
    include: list[str] | None = None
    exclude: list[str] | None = None


class Failure(BaseModel):
    job_id: str
    folder_id: str | None
    database: str | None
    path: str | None
    uri: str | None
    reason: str
    attempts: int
    failed_at: datetime | None


class Reader:
    """The service's state: settings, the search client, the ingester's API."""

    def __init__(
        self,
        settings: Settings,
        *,
        ingester_label: str | None = None,
        search_idle_s: float = 300,
        snippet_chars: int = 300,
    ):
        self.settings = settings
        self.search = SearchService(
            settings.config, search_idle_s=search_idle_s, snippet_chars=snippet_chars
        )
        self.ingester = IngesterAPI(
            settings.ingester_url, settings.auth_token, label=ingester_label
        )
        self.last_request = time.monotonic()
        self.in_flight = 0
        self._reloads: set[asyncio.Task] = set()

    def register(self) -> FolderRegister:
        return FolderRegister.of(self.settings)

    def reload_ingester(self) -> None:
        """Ask the ingester to apply the register, in the background: a reload
        waits for the jobs in flight. A stopped ingester reads it at start."""

        async def reload() -> None:
            try:
                report = await asyncio.to_thread(self.ingester.reload, 600)
            except _UNREACHABLE as error:
                logger.warning("The ingester did not reload: %s", error)
                return
            if report is None:
                await asyncio.to_thread(self.ingester.kick)
            elif report.get("outcome") not in ("applied", "pending"):
                logger.warning("The ingester's reload: %s", report)

        task = asyncio.ensure_future(reload())
        self._reloads.add(task)
        task.add_done_callback(self._reloads.discard)

    async def ingester_view(self) -> tuple[dict | None, dict[str, dict]]:
        """The ingester's `/stats` and its sources by id, or None and {} when
        it does not run, after trying to start it."""
        try:
            if not await asyncio.to_thread(self.ingester.ensure_running):
                return None, {}
            stats = await asyncio.to_thread(self.ingester.stats)
            sources = await asyncio.to_thread(self.ingester.sources)
        except _UNREACHABLE as error:
            logger.info("The ingester's status is unavailable: %s", error)
            return None, {}
        return stats, {s["source_id"]: s for s in sources}

    async def folder_out(
        self, folder: Folder, stats: dict | None, sources: dict[str, dict]
    ) -> FolderOut:
        try:
            documents = await self.search.folder_documents(folder.database, folder.id)
        except Exception as error:  # the database may not exist yet
            logger.info("No document count for %s: %s", folder.id, error)
            documents = 0
        source = sources.get(folder.id)
        last_polled = source.get("last_polled_at") if source else None
        return FolderOut(
            **folder.as_dict(),
            status=FolderStatus(
                watching=source is not None,
                documents=documents,
                queued=(stats or {}).get("queue_depth_by_source", {}).get(folder.id, 0),
                failed=(stats or {}).get("dlq_by_source", {}).get(folder.id, 0),
                last_scan_at=last_polled,
            ),
        )


def _register_error(error: FolderRegisterError) -> HTTPException:
    if isinstance(error, UnknownFolderError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, FolderConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(error))


def build_app(reader: Reader) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if reader.ingester.label:
            await asyncio.to_thread(reader.ingester.kick)
        yield
        await reader.search.aclose()

    app = FastAPI(title="haiku.rag search", lifespan=lifespan)
    token = reader.settings.auth_token

    @app.middleware("http")
    async def activity(request: Request, call_next):
        reader.in_flight += 1
        try:
            return await call_next(request)
        finally:
            reader.in_flight -= 1
            reader.last_request = time.monotonic()

    def authorized(request: Request) -> None:
        if token and request.headers.get("authorization") != f"Bearer {token}":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "a bearer token is required")

    auth = [Depends(authorized)]

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "search_loaded": reader.search.loaded}

    async def run_search(
        q: str,
        limit: int,
        type: Literal["hybrid", "vector", "fts"],
        database: list[str] | None,
        folder: list[str] | None,
        context: bool,
        document_id: str | None = None,
    ) -> SearchResponse:
        try:
            return await reader.search.search(
                q,
                limit=limit,
                search_type=type,
                databases=database or None,
                folders=folder or None,
                document_id=document_id,
                context=context,
            )
        except UnknownDatabaseError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from None

    Limit = Annotated[int, Query(ge=1, le=50)]
    Repeated = Annotated[list[str] | None, Query()]

    @app.get("/search", dependencies=auth, response_model=SearchResponse)
    async def search(
        q: str,
        limit: Limit = 10,
        type: Literal["hybrid", "vector", "fts"] = "hybrid",
        database: Repeated = None,
        folder: Repeated = None,
        context: bool = False,
    ) -> SearchResponse:
        return await run_search(q, limit, type, database, folder, context)

    @app.get(
        "/documents/{document_id}/search", dependencies=auth, response_model=SearchResponse
    )
    async def search_document(
        document_id: str,
        q: str,
        limit: Limit = 10,
        type: Literal["hybrid", "vector", "fts"] = "hybrid",
        database: Repeated = None,
        context: bool = False,
    ) -> SearchResponse:
        return await run_search(q, limit, type, database, None, context, document_id)

    @app.get("/folders", dependencies=auth)
    async def folders() -> dict[str, list[FolderOut]]:
        stats, sources = await reader.ingester_view()
        return {
            "folders": [
                await reader.folder_out(f, stats, sources) for f in reader.register().folders()
            ]
        }

    @app.post("/folders", dependencies=auth, status_code=status.HTTP_201_CREATED)
    async def add_folder(body: NewFolder) -> FolderOut:
        try:
            folder = await asyncio.to_thread(
                reader.register().add,
                body.path,
                database=body.database,
                include=body.include,
                exclude=body.exclude,
                folder_id=body.id,
            )
        except FolderRegisterError as error:
            raise _register_error(error) from None
        reader.reload_ingester()
        return FolderOut(**folder.as_dict())

    @app.patch("/folders/{folder_id}", dependencies=auth)
    async def update_folder(folder_id: str, body: FolderPatch) -> FolderOut:
        try:
            folder = await asyncio.to_thread(
                reader.register().update, folder_id, include=body.include, exclude=body.exclude
            )
        except FolderRegisterError as error:
            raise _register_error(error) from None
        reader.reload_ingester()
        return FolderOut(**folder.as_dict())

    @app.delete("/folders/{folder_id}", dependencies=auth)
    async def remove_folder(folder_id: str, keep_documents: bool = False) -> dict[str, str]:
        try:
            await asyncio.to_thread(
                reader.register().remove, folder_id, keep_documents=keep_documents
            )
        except FolderRegisterError as error:
            raise _register_error(error) from None
        reader.reload_ingester()
        return {"removed": folder_id}

    @app.get("/status", dependencies=auth)
    async def service_status() -> dict[str, Any]:
        stats, _ = await reader.ingester_view()
        depth = sum((stats or {}).get("queue_depth_by_source", {}).values())
        busy = (stats or {}).get("workers", {}).get("busy", 0)
        # A removed folder's dead jobs stay in the queue until they expire.
        registered = {f.id for f in reader.register().folders()}
        dead = (stats or {}).get("dlq_by_source", {})
        return {
            "indexing": depth > 0,
            "queued": max(depth - busy, 0),
            "in_flight": busy,
            "failed": sum(n for source, n in dead.items() if source in registered),
            "paused": False,
            "pause_reason": None,
            "ingester_running": stats is not None,
            "embedder_loaded": reader.search.loaded,
            "databases": await reader.search.document_counts(),
        }

    def failure(job: dict, databases: dict[str, str]) -> Failure:
        return Failure(
            job_id=job["id"],
            folder_id=job.get("source_id"),
            database=databases.get(job.get("source_id", "")),
            path=path_from_uri(job.get("uri")),
            uri=job.get("uri"),
            reason=job.get("last_error") or "failed",
            attempts=job.get("attempts", 0),
            failed_at=job.get("completed_at"),
        )

    async def dead_jobs(folder: str | None, limit: int, offset: int) -> list[dict]:
        stats, _ = await reader.ingester_view()
        if stats is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "the ingester is not running"
            )
        try:
            return await asyncio.to_thread(reader.ingester.dead_jobs, folder, limit, offset)
        except _UNREACHABLE as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from None

    @app.get("/failures", dependencies=auth)
    async def failures(
        folder: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, list[Failure]]:
        databases = {f.id: f.database for f in reader.register().folders()}
        jobs = [
            job
            for job in await dead_jobs(folder, limit, offset)
            if folder is not None or job.get("source_id") in databases
        ]
        jobs.sort(key=lambda job: job.get("completed_at") or "", reverse=True)
        return {"failures": [failure(job, databases) for job in jobs]}

    @app.post("/failures/{job_id}/retry", dependencies=auth)
    async def retry(job_id: str) -> dict[str, list[str]]:
        try:
            job = await asyncio.to_thread(reader.ingester.retry_job, job_id)
        except IngesterError as error:
            raise HTTPException(error.status, error.detail) from None
        except (IngesterUnavailable, TimeoutError) as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from None
        return {"retried": [job["id"]]}

    @app.post("/failures/retry", dependencies=auth)
    async def retry_all(folder: str | None = None) -> dict[str, list[str]]:
        ids = [folder] if folder else [f.id for f in reader.register().folders()]
        retried: list[str] = []
        for source_id in ids:
            jobs = await dead_jobs(source_id, 500, 0)
            if not jobs:
                continue
            try:
                await asyncio.to_thread(reader.ingester.retry_source, source_id)
            except IngesterError as error:
                raise HTTPException(error.status, error.detail) from None
            except (IngesterUnavailable, TimeoutError) as error:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from None
            retried.extend(job["id"] for job in jobs)
        return {"retried": retried}

    @app.exception_handler(FolderRegisterError)
    async def register_error(_: Request, error: FolderRegisterError) -> JSONResponse:
        raised = _register_error(error)
        return JSONResponse({"detail": raised.detail}, status_code=raised.status_code)

    return app


async def _exit_when_idle(server, reader: Reader, idle_exit_s: float) -> None:
    while not server.should_exit:
        await asyncio.sleep(min(30.0, max(idle_exit_s / 4, 0.05)))
        idle = time.monotonic() - reader.last_request
        if reader.in_flight == 0 and idle >= idle_exit_s:
            logger.info("Idle for %.0f s; exiting until the next request", idle)
            server.should_exit = True


def serve(
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    idle_exit_s: float = 900,
    search_idle_s: float = 300,
    ingester_label: str | None = None,
) -> None:
    """Serve on launchd's socket (HAIKU_RAG_LISTEN_FD) or on HOST:PORT."""
    import uvicorn

    reader = Reader(settings, ingester_label=ingester_label, search_idle_s=search_idle_s)
    app = build_app(reader)
    fd = os.environ.get("HAIKU_RAG_LISTEN_FD")
    if fd:
        listener = socket.socket(fileno=int(fd))
        config = uvicorn.Config(app, log_level="info", access_log=False)
        sockets = [listener]
    else:
        config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
        sockets = None
    server = uvicorn.Server(config)

    async def main() -> None:
        watcher = (
            asyncio.ensure_future(_exit_when_idle(server, reader, idle_exit_s))
            if idle_exit_s > 0
            else None
        )
        try:
            await server.serve(sockets=sockets)
        finally:
            if watcher is not None:
                watcher.cancel()

    asyncio.run(main())
