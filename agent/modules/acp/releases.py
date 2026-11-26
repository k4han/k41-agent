"""Fetch and verify ACP CLI artifacts from the upstream release services."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import stat
import tarfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal
from urllib.parse import urlsplit

import httpx

from agent.modules.acp.agents import ANTIGRAVITY_CLI_AGENT_ID
from agent.modules.acp.errors import AcpError
from agent.modules.acp.installations import version_number

GOOGLE_MANIFESTS = "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests"
OPENCODE_RELEASES = "https://api.github.com/repos/anomalyco/opencode/releases"
GOOGLE_DOWNLOAD_HOSTS = {"storage.googleapis.com"}
GITHUB_DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    checksum: str
    algorithm: Literal["sha512", "sha256"]
    archive: Literal["binary", "tar", "zip"]
    member: str


def platform_target() -> tuple[str, str, bool] | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if system not in {"windows", "darwin", "linux"} or arch is None:
        return None
    musl = system == "linux" and (
        "musl" in platform.libc_ver()[0].lower() or any(Path("/lib").glob("ld-musl-*.so.1"))
    )
    return system, arch, musl


def _google_release(client: httpx.Client, system: str, arch: str, musl: bool) -> Release:
    key = f"{system}_{'amd64' if arch == 'x64' else arch}" + ("_musl" if musl else "")
    response = client.get(f"{GOOGLE_MANIFESTS}/{key}.json")
    response.raise_for_status()
    manifest = response.json()
    version, url, checksum = (manifest.get(field) for field in ("version", "url", "sha512"))
    if (
        not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version)
        or version_number(version) < (1, 3, 3, True)
        or not isinstance(url, str) or not _allowed_url(url, GOOGLE_DOWNLOAD_HOSTS)
        or not urlsplit(url).path.startswith("/antigravity-public/antigravity-cli/")
        or not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{128}", checksum)
    ):
        raise AcpError("Google did not provide a valid Antigravity CLI release for this machine.")
    archive = "tar" if urlsplit(url).path.endswith(".tar.gz") else "binary"
    return Release(version, url, checksum.lower(), "sha512", archive, "antigravity")


def _opencode_release(client: httpx.Client, system: str, arch: str, musl: bool) -> Release:
    archive = "tar" if system == "linux" else "zip"
    suffix = ("-musl" if musl else "") + (".tar.gz" if archive == "tar" else ".zip")
    prefix = f"opencode-{system}-{arch}"
    names = [prefix + "-baseline" + suffix, prefix + suffix] if arch == "x64" else [prefix + suffix]
    response = client.get(OPENCODE_RELEASES, params={"per_page": 30}, headers={"Accept": "application/vnd.github+json"})
    response.raise_for_status()
    stable = [
        release for release in response.json()
        if not release["draft"] and not release["prerelease"] and re.fullmatch(r"v2\.\d+\.\d+", release["tag_name"])
    ]
    for release in sorted(stable, key=lambda value: version_number(value["tag_name"]), reverse=True):
        tag = release["tag_name"]
        assets = {asset["name"]: asset for asset in release["assets"]}
        for name in names:
            asset = assets.get(name)
            if asset is None:
                continue
            digest = asset.get("digest") or ""
            url = f"https://github.com/anomalyco/opencode/releases/download/{tag}/{name}"
            if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest) or asset.get("browser_download_url") != url:
                continue
            return Release(tag[1:], url, digest[7:].lower(), "sha256", archive, "opencode.exe" if system == "windows" else "opencode")
    raise AcpError("OpenCode did not provide a verified CLI release for this machine.")


def latest_release(agent_id: str) -> Release:
    target = platform_target()
    if target is None:
        raise AcpError("Downloads are unavailable for this operating system or architecture.")
    try:
        with httpx.Client(timeout=5) as client:
            if agent_id == ANTIGRAVITY_CLI_AGENT_ID:
                return _google_release(client, *target)
            return _opencode_release(client, *target)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
        raise AcpError("Could not check the official ACP release. Try again later.") from None


def _allowed_url(url: str, hosts: set[str]) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc in hosts


def _check_interrupted(stop: threading.Event, deadline: float) -> None:
    if stop.is_set() or time.monotonic() >= deadline:
        raise AcpError("The download was interrupted or timed out.")


def _write_payload(
    source: BinaryIO, target: Path, stop: threading.Event, deadline: float,
) -> None:
    with target.open("xb") as output:
        size = 0
        while chunk := source.read(CHUNK_BYTES):
            _check_interrupted(stop, deadline)
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise AcpError("The extracted executable exceeds the size limit.")
            output.write(chunk)


def _member_matches(name: str, expected: str) -> bool:
    path = PurePosixPath(name)
    return path.name == expected and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _extract_release(release: Release, package: Path, target: Path, stop: threading.Event, deadline: float) -> None:
    if release.archive == "binary":
        os.replace(package, target)
    elif release.archive == "tar":
        with tarfile.open(package, "r:gz") as archive:
            members = [member for member in archive if member.isfile() and _member_matches(member.name, release.member)]
            if len(members) != 1:
                raise AcpError("The download does not contain the expected executable.")
            with archive.extractfile(members[0]) as source:
                _write_payload(source, target, stop, deadline)
    else:
        with zipfile.ZipFile(package) as archive:
            members = [
                member for member in archive.infolist()
                if not member.is_dir() and not stat.S_ISLNK(member.external_attr >> 16)
                and _member_matches(member.filename, release.member)
            ]
            if len(members) != 1:
                raise AcpError("The download does not contain the expected executable.")
            with archive.open(members[0]) as source:
                _write_payload(source, target, stop, deadline)
    if os.name != "nt":
        target.chmod(0o755)


def download_executable(release: Release, target: Path, stop: threading.Event) -> None:
    package = target.parent / "download"
    hosts = GOOGLE_DOWNLOAD_HOSTS if release.algorithm == "sha512" else GITHUB_DOWNLOAD_HOSTS
    deadline = time.monotonic() + 600
    digest = hashlib.new(release.algorithm)
    url = release.url
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=5)) as client:
            for _ in range(5):
                _check_interrupted(stop, deadline)
                if not _allowed_url(url, hosts):
                    raise AcpError("The download redirected outside the official release service.")
                with client.stream("GET", url) as response:
                    if response.is_redirect and response.next_request is not None:
                        url = str(response.next_request.url)
                        continue
                    response.raise_for_status()
                    size = 0
                    with package.open("xb") as output:
                        for chunk in response.iter_bytes(CHUNK_BYTES):
                            _check_interrupted(stop, deadline)
                            size += len(chunk)
                            if size > MAX_DOWNLOAD_BYTES:
                                raise AcpError("The download exceeds the size limit.")
                            digest.update(chunk)
                            output.write(chunk)
                    break
            else:
                raise AcpError("The release service redirected the download too many times.")
        if digest.hexdigest() != release.checksum:
            raise AcpError("The download failed its checksum verification.")
        _extract_release(release, package, target, stop, deadline)
        _check_interrupted(stop, deadline)
    except httpx.HTTPError:
        raise AcpError("Could not download the official ACP release. Try again later.") from None
    except (tarfile.TarError, zipfile.BadZipFile, EOFError):
        raise AcpError("The downloaded ACP archive could not be read.") from None
