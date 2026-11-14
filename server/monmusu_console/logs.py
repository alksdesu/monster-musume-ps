from pathlib import Path


def tail(path: Path, size: int) -> str:
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - size))
        return stream.read().decode("utf-8", "replace")


def listing(folder: Path) -> list[dict]:
    rows = [
        {
            "name": path.name,
            "size": path.stat().st_size,
            "modified": int(path.stat().st_mtime),
        }
        for path in folder.glob("*.log")
    ]
    rows.sort(key=lambda row: row["modified"], reverse=True)
    return rows
