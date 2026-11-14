import gzip
import zlib
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.delivery.http.dashboard.spa import CachedStaticFiles, IMMUTABLE_CACHE_CONTROL

ASSET_PATH = "/dashboard-assets/assets/app-abc123.js"
SOURCE = b"export const ready = true;\n" * 100
BROTLI = bytes.fromhex("1b8b0af8c56dec7b96fb4b37a31c6521033d962a78834ed68f3794fb7243e72b020000")


@pytest.fixture
def asset_client(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-abc123.js").write_bytes(SOURCE)
    (assets / "app-abc123.js.gz").write_bytes(gzip.compress(SOURCE, mtime=0))
    (assets / "app-abc123.js.br").write_bytes(BROTLI)
    app = FastAPI()
    app.mount("/dashboard-assets", CachedStaticFiles(directory=tmp_path))
    with TestClient(app) as client:
        yield client


def request_asset(client, encoding="br, gzip", *, method="GET", headers=None, path=ASSET_PATH):
    request = client.build_request(method, path, headers=headers)
    if encoding is None:
        request.headers.pop("accept-encoding", None)
    else:
        request.headers["accept-encoding"] = encoding
    response = client.send(request, stream=True)
    try:
        body = b"".join(response.iter_raw())
    finally:
        response.close()
    return response, body


@pytest.mark.parametrize(("encoding", "expected"), [
    (None, None),
    ("", None),
    ("identity", None),
    ("deflate", None),
    ("xbr, xgzip", None),
    ("gzip", "gzip"),
    ("br", "br"),
    ("gzip, br", "br"),
    ("BR, GZIP", "br"),
    ("br;q=0.3, gzip;q=0.8", "gzip"),
    ("gzip;q=0.3, br;q=0.8", "br"),
    ("br;q=0, gzip", "gzip"),
    ("br;q=0, gzip;q=0", None),
    ("br;q=invalid, gzip", "gzip"),
    ("br;q=nan, gzip", "gzip"),
    ("br;q=2, gzip", "gzip"),
    ("br;q=-1, gzip", "gzip"),
    ("br;q=0.2, identity;q=0.8", None),
    ("br, identity;q=0", "br"),
    ("*", "br"),
    ("br;q=0, *;q=0.8", "gzip"),
    ("gzip, *;q=0", "gzip"),
    ("*;q=0, identity;q=1", None),
])
def test_precompressed_encoding_negotiation(asset_client, tmp_path, encoding, expected):
    response, body = request_asset(asset_client, encoding)

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == expected
    assert "javascript" in response.headers["content-type"]
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL
    assert int(response.headers["content-length"]) == len(body)
    if expected == "br":
        assert body == BROTLI
    elif expected == "gzip":
        assert body == (tmp_path / "assets/app-abc123.js.gz").read_bytes()
        assert gzip.decompress(body) == SOURCE
    else:
        assert body == SOURCE


def test_missing_variants_fall_back_without_compressing(asset_client, tmp_path, monkeypatch):
    def fail_compression(*args, **kwargs):
        raise AssertionError("Static requests must not compress data at runtime")

    monkeypatch.setattr(zlib, "compressobj", fail_compression)
    (tmp_path / "assets/app-abc123.js.br").unlink()
    response, _ = request_asset(asset_client)
    assert response.headers["content-encoding"] == "gzip"

    (tmp_path / "assets/app-abc123.js.gz").unlink()
    response, body = request_asset(asset_client)
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert body == SOURCE

    response, _ = request_asset(asset_client, "br, gzip, identity;q=0")
    assert response.status_code == 406


@pytest.mark.parametrize("encoding", ["*;q=0", "br;q=0, gzip;q=0, identity;q=0"])
def test_unacceptable_representations_return_406(asset_client, encoding):
    response, body = request_asset(asset_client, encoding)
    assert response.status_code == 406
    assert response.headers["vary"] == "Accept-Encoding"
    assert "cache-control" not in response.headers
    assert body == b""


@pytest.mark.parametrize("encoding", ["identity", "gzip", "br"])
def test_precompressed_assets_support_head_and_cache_validation(asset_client, encoding):
    response, body = request_asset(asset_client, encoding)
    etag = response.headers["etag"]

    head, head_body = request_asset(asset_client, encoding, method="HEAD")
    assert head.status_code == 200
    assert head_body == b""
    assert int(head.headers["content-length"]) == len(body)
    assert head.headers["etag"] == etag
    assert head.headers.get("content-encoding") == response.headers.get("content-encoding")

    for headers in ({"If-None-Match": etag}, {"If-Modified-Since": response.headers["last-modified"]}):
        revalidated, revalidated_body = request_asset(asset_client, encoding, headers=headers)
        assert revalidated.status_code == 304
        assert revalidated_body == b""
        assert revalidated.headers["etag"] == etag
        assert revalidated.headers["vary"] == "Accept-Encoding"
        assert revalidated.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL


def test_etags_distinguish_representations(asset_client):
    etags = set()
    for encoding in ("identity", "gzip", "br"):
        response, _ = request_asset(asset_client, encoding)
        etags.add(response.headers["etag"])
        for other in ("identity", "gzip", "br"):
            if other == encoding:
                continue
            other_response, _ = request_asset(asset_client, other, headers={"If-None-Match": response.headers["etag"]})
            assert other_response.status_code == 200
    assert len(etags) == 3


def test_compressed_wasm_keeps_original_mime_type(asset_client, tmp_path):
    wasm_path = tmp_path / "assets/engine-abc123.wasm"
    source = b"\x00asm" + bytes(4096)
    wasm_path.write_bytes(source)
    wasm_path.with_suffix(".wasm.gz").write_bytes(gzip.compress(source))

    response, body = request_asset(asset_client, "gzip", path="/dashboard-assets/assets/engine-abc123.wasm")
    assert response.headers["content-type"] == "application/wasm"
    assert response.headers["content-encoding"] == "gzip"
    assert gzip.decompress(body) == source


def test_byte_ranges_use_original_representation(asset_client):
    response, body = request_asset(asset_client, headers={"Range": "bytes=0-9"})
    assert response.status_code == 206
    assert "content-encoding" not in response.headers
    assert response.headers["content-range"] == f"bytes 0-9/{len(SOURCE)}"
    assert body == SOURCE[:10]

    response, body = request_asset(asset_client, headers={"Range": "bytes=0-9,20-29"})
    assert response.status_code == 206
    assert "content-encoding" not in response.headers
    assert response.headers["content-type"].startswith("multipart/byteranges")
    assert SOURCE[:10] in body


def test_sidecars_do_not_bypass_original_file_or_method_checks(asset_client, tmp_path):
    response, _ = request_asset(asset_client, method="POST")
    assert response.status_code == 405
    (tmp_path / "assets/app-abc123.js").unlink()
    response, _ = request_asset(asset_client)
    assert response.status_code == 404
    response, _ = request_asset(asset_client, "*;q=0")
    assert response.status_code == 404


def test_bootstrap_mount_serves_precompressed_assets_without_runtime_compression(
    tmp_path, monkeypatch, isolated_container,
):
    from agent.bootstrap import app as app_module
    from agent.delivery.http.dashboard import spa

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app-abc123.js").write_bytes(SOURCE)
    (tmp_path / "assets/app-abc123.js.br").write_bytes(BROTLI)
    monkeypatch.setattr(spa, "STATIC_DIR", tmp_path)

    def fail_compression(*args, **kwargs):
        raise AssertionError("Static requests must not compress data at runtime")

    monkeypatch.setattr(zlib, "compressobj", fail_compression)
    app = app_module.create_app(
        bootstrap_config=replace(isolated_container.bootstrap_config, enable_dashboard=True),
        container=isolated_container,
    )
    client = TestClient(app)
    response, body = request_asset(client)
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "br"
    assert body == BROTLI

    response, body = request_asset(client, "gzip")
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert body == SOURCE
