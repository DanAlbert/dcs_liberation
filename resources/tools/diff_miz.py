"""Diffs two mission files.

The DCS mission editor and pydcs don't necessarily serialize the two mission
structures in the same order, so we can't just diff them. This performs a
structure-aware diff of two mission files.
"""
import argparse
import collections
from contextlib import contextmanager
import difflib
import json
import logging
import os
from pathlib import Path
from typing import (
    Any,
    ContextManager,
    Dict,
    Tuple,
)
from zipfile import ZipFile

import lupa

THIS_DIR = Path(__file__).parent.resolve()


@contextmanager
def cd(path: Path):
    cwd = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(cwd)


@contextmanager
def dcs_lua_context(dcs_path: Path) -> ContextManager[lupa.LuaRuntime]:
    with cd(dcs_path):
        yield lupa.LuaRuntime()


def load_mission_data(dcs_path: Path, mission: Path) -> Dict[Any, Any]:
    with ZipFile(mission) as archive:
        contents = archive.read("mission").decode("utf-8")
    with dcs_lua_context(dcs_path) as lua:
        lua.execute(contents)
        return lua.eval("mission")


def permissive_sort(val: Tuple[Any, Any]) -> Tuple[int, Any]:
    try:
        return 0, int(val[0])
    except ValueError:
        return 1, val[0]


def convert_lua_table(obj: Any) -> Dict[Any, Any]:
    sorted_result = collections.OrderedDict()
    for key in sorted(dict(obj), key=permissive_sort):
        sorted_result[key] = obj[key]
    return sorted_result


def convert_and_sort_lua_table(item: Any) -> Any:
    result = collections.OrderedDict()
    for key, val in sorted(item.items(), key=permissive_sort):
        if isinstance(val, dict):
            result[key] = convert_and_sort_lua_table(val)
        elif val.__class__.__name__ == "_LuaTable":
            result[key] = convert_and_sort_lua_table(val)
        else:
            result[key] = val
    return result


class Differ:
    """Imports beacon definitions from each available terrain mod.

    Only beacons for maps owned by the user can be imported. Other maps that
    have been previously imported will not be disturbed.
    """

    def __init__(self, dcs_path: Path,
                 mission_a: Path, mission_b: Path) -> None:
        self.dcs_path = dcs_path
        self.mission_a = mission_a
        self.mission_b = mission_b

    def run(self) -> None:
        """Exports the beacons for each available terrain mod."""
        data_a = load_mission_data(self.dcs_path, self.mission_a)
        data_b = load_mission_data(self.dcs_path, self.mission_b)

        diff = difflib.unified_diff(
            json.dumps(convert_and_sort_lua_table(data_a), indent=True).splitlines(),
            json.dumps(convert_and_sort_lua_table(data_b), indent=True).splitlines(),
        )
        for line in diff:
            print(line)


def parse_args() -> argparse.Namespace:
    """Parses and returns command line arguments."""
    parser = argparse.ArgumentParser()

    def resolved_path(val: str) -> Path:
        """Returns the given string as a fully resolved Path."""
        return Path(val).resolve()

    parser.add_argument(
        "dcs_path",
        metavar="DCS_PATH",
        type=resolved_path,
        help="Path to DCS installation."
    )

    parser.add_argument(
        "mission_a",
        metavar="MISSION_A",
        type=resolved_path,
        help="Path to the first mission file."
    )

    parser.add_argument(
        "mission_b",
        metavar="MISSION_B",
        type=resolved_path,
        help="Path to the second mission file."
    )

    return parser.parse_args()


def main() -> None:
    """Program entry point."""
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG)
    Differ(args.dcs_path, args.mission_a, args.mission_b).run()


if __name__ == "__main__":
    main()
