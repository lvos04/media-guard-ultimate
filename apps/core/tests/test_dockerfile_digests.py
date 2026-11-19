"""De controle weigert zwevende externe images en accepteert interne bouwstages."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("regel", [
    "FROM python:3.12.16-slim-bookworm",
    "FROM debian:bookworm@sha256:1234",
    "ARG BASE_IMAGE=ubuntu:26.04\nFROM ${BASE_IMAGE}",
    "ARG OPTIONAL_IMAGE=ubuntu:26.04\nFROM scratch",
    "FROM --platform=linux/amd64 \\\n  ubuntu:26.04 AS runtime",
    "FROM ${ONBEKENDE_BASIS}",
    "FROM ghcr.io/lvos04/mediaguard-web:dev",
])
def test_weigert_een_externe_basis_zonder_pin(tmp_path, regel):
    dockerfile = tmp_path / "Dockerfile.proef"
    dockerfile.write_text(regel + "\n", encoding="utf-8")
    resultaat = subprocess.run(
        [sys.executable, "scripts/check-dockerfile-digests.py", str(dockerfile)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert resultaat.returncode == 1
    assert "vereist een versietag en een volledige sha256-digest" in resultaat.stderr


def test_accepteert_gepinde_basis_en_interne_stages(tmp_path):
    dockerfile = tmp_path / "Dockerfile.proef"
    dockerfile.write_text(
        f"ARG BASE_IMAGE=ubuntu:26.04@sha256:{'a' * 64}\n"
        "FROM ${BASE_IMAGE} AS build\nFROM scratch AS leeg\nFROM build AS runtime\n",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, "scripts/check-dockerfile-digests.py", str(dockerfile)],
        cwd=ROOT, check=True,
    )
