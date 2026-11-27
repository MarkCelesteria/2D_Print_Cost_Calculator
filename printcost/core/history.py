import json
import os
import shutil
import threading
import time
import uuid

from printcost.core import pricing

MAX_ENTRIES = 10
MAX_AGE_DAYS = 7
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024

_lock = threading.Lock()


def _root():
    path = os.path.join(pricing.user_data_dir(), "history")
    os.makedirs(path, exist_ok=True)
    return path


def _index_path():
    return os.path.join(_root(), "index.json")


def _folder(entry_id):
    return os.path.join(_root(), entry_id)


def _load():
    try:
        with open(_index_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (OSError, ValueError):
        pass
    return []


def _save(entries):
    tmp = _index_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f)
    os.replace(tmp, _index_path())


def stored_paths(entry):
    return [os.path.join(_folder(entry["id"]), f["path"]) for f in entry.get("files", [])]


def _norm(paths):
    return [os.path.normcase(os.path.abspath(p)) for p in paths]


def _remove(entry):
    shutil.rmtree(_folder(entry["id"]), ignore_errors=True)


def _complete(entry):
    paths = stored_paths(entry)
    return bool(paths) and all(os.path.isfile(p) for p in paths)


def _size(entry):
    return sum(f.get("size", 0) for f in entry.get("files", []))


def _purge(entries):
    now = time.time()
    keep = []
    for e in entries:
        if now - e.get("ts", 0) > MAX_AGE_DAYS * 86400 or not _complete(e):
            _remove(e)
        else:
            keep.append(e)
    while len(keep) > MAX_ENTRIES:
        _remove(keep.pop())
    while len(keep) > 1 and sum(_size(e) for e in keep) > MAX_TOTAL_BYTES:
        _remove(keep.pop())
    return keep


def _refresh(entries, entry, options, total, printed):
    entry.update(ts=time.time(), options=options, total=total, printed=printed)
    entries.remove(entry)
    entries.insert(0, entry)
    _save(entries)


def _add(paths, options, total, printed):
    folder = None
    try:
        if not paths or any(not os.path.isfile(p) or os.path.getsize(p) > MAX_FILE_BYTES for p in paths):
            return
        if sum(os.path.getsize(p) for p in paths) > MAX_TOTAL_BYTES:
            return
        key = [[os.path.normcase(os.path.abspath(p)), os.path.getsize(p), os.stat(p).st_mtime_ns] for p in paths]
        with _lock:
            entries = _load()
            wanted = _norm(paths)
            for e in entries:
                if e.get("key") == key or _norm(stored_paths(e)) == wanted:
                    _refresh(entries, e, options, total, printed)
                    return
        entry_id = uuid.uuid4().hex[:10]
        folder = _folder(entry_id)
        files = []
        for i, p in enumerate(paths):
            name = os.path.basename(p)
            os.makedirs(os.path.join(folder, str(i)))
            dest = os.path.join(folder, str(i), name)
            shutil.copy2(p, dest)
            files.append({"name": name, "path": f"{i}/{name}", "size": os.path.getsize(dest)})
        entry = {"id": entry_id, "ts": time.time(), "key": key, "files": files,
                 "options": options, "total": total, "printed": printed}
        with _lock:
            _save(_purge([entry] + _load()))
        folder = None
    except Exception:
        pass
    finally:
        if folder:
            shutil.rmtree(folder, ignore_errors=True)


def add(paths, options, total, printed=0):
    t = threading.Thread(target=_add, args=(list(paths), options, total, printed), daemon=True)
    t.start()
    return t


def entries():
    with _lock:
        data = _load()
        kept = _purge(data)
        if len(kept) != len(data):
            _save(kept)
        return kept


def delete(entry_id):
    with _lock:
        data = _load()
        for e in data:
            if e.get("id") == entry_id:
                _remove(e)
        _save([e for e in data if e.get("id") != entry_id])


def clear():
    with _lock:
        for e in _load():
            _remove(e)
        _save([])


def cleanup():
    try:
        with _lock:
            data = _load()
            kept = _purge(data)
            if len(kept) != len(data):
                _save(kept)
            ids = {e["id"] for e in kept}
            root = _root()
            for name in os.listdir(root):
                path = os.path.join(root, name)
                if os.path.isdir(path) and name not in ids and time.time() - os.path.getmtime(path) > 3600:
                    shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass