import json
from collections.abc import Iterable, Iterator
from pathlib import Path


SOURCE, TARGET = "source", "target"


class Memory:
    def __init__(self, path: Path):
        self.path = path
        self._known: dict[str, str] = {}
        self._file = None
        if path.exists():
            with path.open(encoding="utf-8") as file:
                for line in file:
                    if line.strip():
                        pair = json.loads(line)
                        # A later line corrects an earlier one, so the file can be amended by appending.
                        self._known[pair[SOURCE]] = pair[TARGET]

    def __enter__(self) -> "Memory":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8", newline="\n")
        return self

    def __exit__(self, *raised) -> None:
        self._file.close()
        self._file = None

    def __len__(self) -> int:
        return len(self._known)

    def __contains__(self, source: str) -> bool:
        return source in self._known

    def get(self, source: str) -> str | None:
        return self._known.get(source)

    def items(self) -> Iterator[tuple[str, str]]:
        return iter(self._known.items())

    def record(self, pairs: Iterable[tuple[str, str]]) -> None:
        if self._file is None:
            raise RuntimeError("the memory is not open for writing")
        for source, target in pairs:
            self._known[source] = target
            self._file.write(
                json.dumps({SOURCE: source, TARGET: target}, ensure_ascii=False) + "\n"
            )
        # A run of hours must not lose what it has paid for when it is interrupted.
        self._file.flush()

    def rewrite(self, pairs: Iterable[tuple[str, str]]) -> None:
        if self._file is not None:
            raise RuntimeError("the memory is open for appending")
        for source, target in pairs:
            self._known[source] = target
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staged = self.path.with_suffix(".jsonl.partial")
        with staged.open("w", encoding="utf-8", newline="\n") as file:
            for source, target in self._known.items():
                file.write(
                    json.dumps({SOURCE: source, TARGET: target}, ensure_ascii=False) + "\n"
                )
        staged.replace(self.path)
