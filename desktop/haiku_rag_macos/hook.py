"""The `macos-folders` hook: deletes a removed folder's documents inside
haiku-ingester.

haiku.rag leaves a retired source's documents in place ("delete its documents
by hand if they should go"). A folder removed without keeping its documents is
listed in the register's `.retired.yaml`; when haiku-ingester starts a
database, which a reload does after the folder's source left it, this hook's
`ingester_lifespan` deletes the documents those folders own, with the
ingester's own writable client and before the database's first job, so the
ingester stays the database's one writer. A folder registered again in the
meantime keeps its documents.

Activated in haiku.rag.yaml:

    hooks:
      - name: macos-folders
        options: {directory: <the databases_include directory>}
"""

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from haiku.rag.hooks import Hook

from haiku_rag_macos.folders import FolderRegister

if TYPE_CHECKING:
    from haiku.rag.client import HaikuRAG

# Under haiku.rag's logger, which haiku-ingester writes to its log.
logger = logging.getLogger("haiku.rag.hooks.macos_folders")


def source_filter(source_id: str) -> str:
    """SQL filter on documents whose metadata names `source_id` as their source."""
    value = json.dumps(source_id)[1:-1]
    value = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    value = value.replace("'", "''")
    return f"metadata LIKE '%\"source_id\": \"{value}\"%' ESCAPE '\\'"


async def delete_source_documents(client: "HaikuRAG", source_id: str) -> int:
    """Delete every document `source_id` owns, attachments with them."""
    deleted = 0
    for document in await client.list_documents(filter=source_filter(source_id)):
        if (document.metadata or {}).get("source_id") != source_id or document.id is None:
            continue
        if await client.delete_document(document.id, source_id=source_id):
            deleted += 1
    return deleted


class FolderRetirement(Hook):
    def __init__(self, directory: str):
        self.directory = Path(directory)

    async def retire(self, client: "HaikuRAG") -> None:
        database = client.source
        if database is None:
            return
        register = FolderRegister(self.directory, [database])
        pending = [r for r in register.retired() if r.database == database]
        if not pending:
            return
        registered = {f.id for f in register.folders() if f.database == database}
        done: set[str] = set()
        for folder in pending:
            if folder.id in registered:
                logger.info("Folder %s is registered again; its documents stay", folder.id)
            else:
                count = await delete_source_documents(client, folder.id)
                logger.info(
                    "Removed folder %s: deleted its %d document(s) from %s",
                    folder.id,
                    count,
                    database,
                )
            done.add(folder.id)
        register.settle_retired(database, done)

    @asynccontextmanager
    async def ingester_lifespan(self, client: "HaikuRAG") -> AsyncGenerator[None]:
        try:
            await self.retire(client)
        except Exception:
            # The database still starts; the next start tries again.
            logger.exception("Could not delete the documents of removed folders")
        yield
