from pathlib import Path

from backend.config import (
    PREFERRED_START_NAMES,
    PREFERRED_STOP_NAMES,
    SCRIPT_EXTENSIONS,
    START_KEYWORDS,
    STOP_KEYWORDS,
)


def _is_script(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SCRIPT_EXTENSIONS


def _matches_keywords(name: str, keywords: tuple[str, ...]) -> bool:
    lower = name.lower()
    stem = Path(name).stem.lower()
    for kw in keywords:
        kw_l = kw.lower()
        if stem == kw_l or stem.startswith(kw_l) or kw_l in stem:
            return True
        if kw in name:  # Chinese keywords keep original case
            return True
    return False


def _priority(path: Path, preferred: tuple[str, ...], keywords: tuple[str, ...]) -> int:
    name = path.name
    lower = name.lower()
    for i, pref in enumerate(preferred):
        if lower == pref.lower():
            return i
    stem = path.stem.lower()
    for i, kw in enumerate(keywords):
        if stem == kw.lower() or path.stem == kw:
            return 100 + i
    return 200


def scan_directory(directory: str | Path) -> dict:
    """
    Scan a directory for start/stop scripts.
    Returns candidates sorted by preference.
    """
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在: {root}")

    start_candidates: list[Path] = []
    stop_candidates: list[Path] = []
    other_scripts: list[Path] = []

    for path in sorted(root.iterdir()):
        if not _is_script(path):
            continue
        name = path.name
        is_start = _matches_keywords(name, START_KEYWORDS)
        is_stop = _matches_keywords(name, STOP_KEYWORDS)
        # Avoid double-classifying obvious stop as start
        if is_stop and not is_start:
            stop_candidates.append(path)
        elif is_start and not is_stop:
            start_candidates.append(path)
        elif is_start and is_stop:
            # e.g. start-stop.bat — put in both with lower confidence via other
            other_scripts.append(path)
        else:
            other_scripts.append(path)

    start_candidates.sort(key=lambda p: _priority(p, PREFERRED_START_NAMES, START_KEYWORDS))
    stop_candidates.sort(key=lambda p: _priority(p, PREFERRED_STOP_NAMES, STOP_KEYWORDS))

    def rel(p: Path) -> str:
        return p.name  # top-level only for v1

    return {
        "directory": str(root),
        "start_scripts": [rel(p) for p in start_candidates],
        "stop_scripts": [rel(p) for p in stop_candidates],
        "other_scripts": [rel(p) for p in other_scripts],
        "suggested_start": rel(start_candidates[0]) if start_candidates else (
            rel(other_scripts[0]) if other_scripts else None
        ),
        "suggested_stop": rel(stop_candidates[0]) if stop_candidates else None,
    }
