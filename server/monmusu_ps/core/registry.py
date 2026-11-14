from collections.abc import Callable, Mapping
import logging
from pathlib import Path
import sys
from types import MappingProxyType, ModuleType
import uuid


LOGGER = logging.getLogger("monmusu.features")
FEATURE_PACKAGE = "monmusu_ps.features"


class FeatureRegistry:
    def __init__(self, directory: Path, contracts: dict):
        self.directory = directory.resolve()
        self.contracts = contracts
        self._handlers: Mapping[str, Callable] = MappingProxyType({})
        self._modules: dict[str, ModuleType] | None = None
        self._fingerprint: tuple | None = None
        self.error: str | None = None
        self.refresh()

    @property
    def handlers(self) -> Mapping[str, Callable]:
        return self._handlers

    def _snapshot(self) -> tuple:
        files = []
        for path in sorted(self.directory.iterdir()):
            if path.name.startswith("_") or path.suffix != ".py" or not path.is_file():
                continue
            stat = path.stat()
            files.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(files)

    @staticmethod
    def _discard(modules: Mapping[str, ModuleType]) -> None:
        for name, module in modules.items():
            if sys.modules.get(name) is module:
                del sys.modules[name]

    def _load(self, fingerprint: tuple) -> tuple[dict, dict]:
        revision = uuid.uuid4().hex
        modules, handlers, owners = {}, {}, {}
        try:
            for filename, _, _ in fingerprint:
                path = self.directory / filename
                code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
                name = f"{FEATURE_PACKAGE}._reload_{revision}_{path.stem}"
                module = ModuleType(name)
                module.__file__ = str(path)
                module.__package__ = FEATURE_PACKAGE
                modules[name] = module
                sys.modules[name] = module
                exec(code, module.__dict__)
                routes = module.__dict__.get("ROUTES")
                if not isinstance(routes, Mapping):
                    raise ValueError(f"{filename}: ROUTES must be a mapping")
                for endpoint, handler in routes.items():
                    if endpoint not in self.contracts:
                        raise ValueError(f"{filename}: unknown endpoint {endpoint!r}")
                    if endpoint in handlers:
                        raise ValueError(
                            f"{filename}: duplicate endpoint {endpoint!r} from {owners[endpoint]}"
                        )
                    if not callable(handler):
                        raise ValueError(
                            f"{filename}: handler for {endpoint!r} must be callable"
                        )
                    handlers[endpoint] = handler
                    owners[endpoint] = filename
        except Exception:
            self._discard(modules)
            raise
        return modules, handlers

    def refresh(self) -> None:
        try:
            fingerprint = self._snapshot()
            if fingerprint == self._fingerprint:
                return
            self._fingerprint = fingerprint
            modules, handlers = self._load(fingerprint)
        except Exception as error:
            if self._modules is None:
                raise
            LOGGER.exception("Feature reload failed; keeping the previous handlers")
            self.error = f"{type(error).__name__}: {error}"
            return
        self.error = None
        previous = self._modules
        self._handlers = MappingProxyType(handlers)
        self._modules = modules
        if previous is not None:
            self._discard(previous)
        LOGGER.info(
            "Loaded %s feature modules with %s endpoints", len(modules), len(handlers)
        )

    def close(self) -> None:
        if self._modules is not None:
            self._discard(self._modules)
