import { createHash } from "crypto";
import { readdir, rm, stat, utimes } from "fs/promises";
import { join } from "path";

/** Bumped when rendering changes, so older images are not reused. */
export const RENDER_VERSION = 1;
export const CACHE_CAP_BYTES = 200 * 1024 * 1024;

/** A file name for what was rendered from `path` as it is on disk now. */
export async function cacheKey(path: string, parts: unknown[]): Promise<string> {
  const { mtimeMs, size } = await stat(path);
  return createHash("sha1")
    .update(JSON.stringify([RENDER_VERSION, path, mtimeMs, size, ...parts]))
    .digest("hex")
    .slice(0, 24);
}

/** The cached file, marked as recently used; undefined when it is not cached. */
export async function cached(file: string): Promise<string | undefined> {
  try {
    const now = new Date();
    await utimes(file, now, now);
    return file;
  } catch {
    return undefined;
  }
}

/** Removes the least recently used files until the folder holds at most `cap` bytes. */
export async function prune(dir: string, cap = CACHE_CAP_BYTES): Promise<number> {
  let names: string[];
  try {
    names = await readdir(dir);
  } catch {
    return 0;
  }
  const files = [];
  for (const name of names) {
    try {
      const s = await stat(join(dir, name));
      if (s.isFile()) files.push({ file: join(dir, name), size: s.size, used: s.mtimeMs });
    } catch {
      // removed meanwhile
    }
  }
  let total = files.reduce((n, f) => n + f.size, 0);
  let removed = 0;
  for (const f of files.sort((a, b) => a.used - b.used)) {
    if (total <= cap) break;
    await rm(f.file, { force: true });
    total -= f.size;
    removed += 1;
  }
  return removed;
}
