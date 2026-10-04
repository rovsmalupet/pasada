"""Loads config/params.yaml and config/data_manifest.yaml.

Every other module gets parameter values and input file paths through this module,
never by hardcoding them. Run ``python -m sumo_track.config`` to print every
parameter and which datasets are still placeholders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
PARAMS_PATH = REPO_ROOT / "config" / "params.yaml"
MANIFEST_PATH = REPO_ROOT / "config" / "data_manifest.yaml"

PARAM_STATUSES = ("placeholder", "assumed", "literature", "official", "observed")
DATASET_ACTIVE = ("placeholder", "real", "none")

_PARAM_REQUIRED = {"value", "unit", "status", "source"}
_PARAM_OPTIONAL = {"notes"}
_DATASET_REQUIRED = {"active", "placeholder", "real"}
_DATASET_OPTIONAL = {"provider", "notes"}


class ConfigError(Exception):
    """A config file is missing, malformed, or points to something that doesn't exist."""


@dataclass(frozen=True)
class Param:
    """One entry of params.yaml."""

    name: str
    value: Any
    unit: str
    status: str
    source: str
    notes: str = ""


@dataclass(frozen=True)
class Dataset:
    """One entry of data_manifest.yaml. Paths are relative to the repository root."""

    name: str
    active: str
    placeholder: str | None
    real: str | None
    provider: str = ""
    notes: str = ""

    @property
    def active_source(self) -> str | None:
        """Relative path of the active source, or None when ``active`` is ``none``."""
        if self.active == "placeholder":
            return self.placeholder
        if self.active == "real":
            return self.real
        return None

    @property
    def active_path(self) -> Path | None:
        """Absolute path of the active source, or None when ``active`` is ``none``."""
        source = self.active_source
        return None if source is None else REPO_ROOT / source


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: expected named entries at the top level")
    return data


def _check_keys(file: str, name: str, entry: Any, required: set[str], optional: set[str]) -> None:
    if not isinstance(entry, dict):
        raise ConfigError(f"{file}: '{name}' must be a mapping, got {type(entry).__name__}")
    missing = required - entry.keys()
    if missing:
        raise ConfigError(f"{file}: '{name}' is missing {sorted(missing)}")
    unknown = entry.keys() - required - optional
    if unknown:
        raise ConfigError(f"{file}: '{name}' has unknown keys {sorted(unknown)} (typo?)")


def load_params(path: Path = PARAMS_PATH) -> dict[str, Param]:
    """Read params.yaml and check that every entry carries value, unit, status, and source."""
    params = {}
    for name, entry in _read_yaml(path).items():
        _check_keys(path.name, name, entry, _PARAM_REQUIRED, _PARAM_OPTIONAL)
        if entry["value"] is None:
            raise ConfigError(f"{path.name}: '{name}' has no value")
        if entry["status"] not in PARAM_STATUSES:
            raise ConfigError(
                f"{path.name}: '{name}' has status '{entry['status']}', expected one of {PARAM_STATUSES}"
            )
        for key in ("unit", "source"):
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise ConfigError(f"{path.name}: '{name}' needs a non-empty '{key}'")
        params[name] = Param(
            name=name,
            value=entry["value"],
            unit=entry["unit"],
            status=entry["status"],
            source=entry["source"],
            notes=entry.get("notes") or "",
        )
    return params


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Dataset]:
    """Read data_manifest.yaml and check that every active dataset has a path."""
    datasets = {}
    for name, entry in _read_yaml(path).items():
        _check_keys(path.name, name, entry, _DATASET_REQUIRED, _DATASET_OPTIONAL)
        active = entry["active"]
        if active not in DATASET_ACTIVE:
            raise ConfigError(
                f"{path.name}: '{name}' has active '{active}', expected one of {DATASET_ACTIVE}"
            )
        if active != "none" and not entry[active]:
            raise ConfigError(f"{path.name}: '{name}' is active: {active} but its '{active}' path is empty")
        datasets[name] = Dataset(
            name=name,
            active=active,
            placeholder=entry["placeholder"],
            real=entry["real"],
            provider=entry.get("provider") or "",
            notes=entry.get("notes") or "",
        )
    return datasets


class Config:
    """Parameters and dataset sources, loaded and validated together."""

    def __init__(self, params: dict[str, Param], datasets: dict[str, Dataset]):
        self.params = params
        self.datasets = datasets
        self._warned: set[str] = set()

    @classmethod
    def load(cls, params_path: Path = PARAMS_PATH, manifest_path: Path = MANIFEST_PATH) -> Config:
        return cls(load_params(params_path), load_manifest(manifest_path))

    def param(self, name: str) -> Any:
        """Value of a parameter from params.yaml."""
        try:
            return self.params[name].value
        except KeyError:
            raise ConfigError(f"Parameter '{name}' is not defined in params.yaml") from None

    def dataset_path(self, name: str, must_exist: bool = True) -> Path:
        """Absolute path of a dataset's active source from data_manifest.yaml.

        Logs a warning the first time a placeholder dataset is requested.
        """
        try:
            dataset = self.datasets[name]
        except KeyError:
            raise ConfigError(f"Dataset '{name}' is not defined in data_manifest.yaml") from None
        path = dataset.active_path
        if path is None:
            raise ConfigError(f"Dataset '{name}' is not active (active: none)")
        if must_exist and not path.exists():
            raise ConfigError(f"Dataset '{name}' ({dataset.active}) file not found: {dataset.active_source}")
        if dataset.active == "placeholder" and name not in self._warned:
            log.warning("Dataset '%s' is using PLACEHOLDER data: %s", name, dataset.active_source)
            self._warned.add(name)
        return path

    def placeholder_datasets(self) -> list[str]:
        return [name for name, d in self.datasets.items() if d.active == "placeholder"]

    def placeholder_params(self) -> list[str]:
        return [name for name, p in self.params.items() if p.status == "placeholder"]

    @property
    def is_dev(self) -> bool:
        """True if anything placeholder is active. Such runs are tagged dev and
        must not be used for thesis results."""
        return bool(self.placeholder_datasets() or self.placeholder_params())

    def summary(self) -> str:
        param_rows = [[p.name, str(p.value), p.unit, p.status] for p in self.params.values()]
        dataset_rows = []
        for d in self.datasets.values():
            path = d.active_path
            exists = "-" if path is None else ("yes" if path.exists() else "NO")
            dataset_rows.append([d.name, d.active, d.active_source or "-", exists])

        lines = [
            f"Parameters ({len(self.params)})",
            _table(["NAME", "VALUE", "UNIT", "STATUS"], param_rows),
            "",
            f"Datasets ({len(self.datasets)})",
            _table(["NAME", "ACTIVE", "PATH", "EXISTS"], dataset_rows),
            "",
        ]
        if self.placeholder_datasets():
            lines.append(f"WARNING: placeholder datasets active: {', '.join(self.placeholder_datasets())}")
        if self.placeholder_params():
            lines.append(f"WARNING: placeholder parameters: {', '.join(self.placeholder_params())}")
        if self.is_dev:
            lines.append("Run tag: dev (placeholder data in use; not for thesis results)")
        else:
            lines.append("No placeholders active: runs can be used for thesis results.")
        return "\n".join(lines)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(cell) for cell in column) for column in zip(headers, *rows)]
    return "\n".join(
        "  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in [headers, *rows]
    )


def main() -> None:
    try:
        config = Config.load()
    except ConfigError as e:
        raise SystemExit(f"CONFIG ERROR: {e}")
    print(config.summary())


if __name__ == "__main__":
    main()
