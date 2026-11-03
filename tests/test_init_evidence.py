"""The init corpus describes the machine it ran on, on Linux as on macOS."""

from __future__ import annotations

import os
import subprocess
from typing import Any

import pytest

import init_evidence


def _run_without_sysctl(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    if command[0] == "sysctl":
        raise FileNotFoundError(2, "No such file or directory", "sysctl")
    return subprocess.CompletedProcess(command, 0, "", "")


def test_the_hardware_is_described_without_sysctl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init_evidence.subprocess, "run", _run_without_sysctl)
    uname = os.uname()

    assert init_evidence.hardware() == f"{uname.sysname} {uname.release} {uname.machine}"


def test_macos_adds_the_sysctl_fields_it_can_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def sysctl(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        found = {"hw.model": "Mac0,0\n", "hw.memsize": ""}
        value = found.get(command[-1])
        return subprocess.CompletedProcess(command, 0 if value is not None else 1, value or "", "")

    monkeypatch.setattr(init_evidence.subprocess, "run", sysctl)
    uname = os.uname()

    assert init_evidence.hardware() == f"{uname.sysname} {uname.release} {uname.machine}\nMac0,0"
