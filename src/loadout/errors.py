from __future__ import annotations


class LoadoutError(Exception):
    """Any error loadout raises deliberately."""


class UsageError(LoadoutError):
    pass
