"""Fetch verified official GHCR OCI archives when daemon header timeouts are too short."""

import argparse
import concurrent.futures
import hashlib
import io
import json
import subprocess
import tarfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".external/image-transfer"
DOCKER = "C:/Program Files/Docker/Docker/resources/bin/docker.exe"
LOCKS = {}
LOCKS_GUARD = threading.Lock()
ACCEPT = (
    "application/vnd.oci.image.index.v1+json,application/vnd.oci.image.manifest.v1+json,"
    "application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json"
)


def verified(data, digest):
    if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Registry digest mismatch")


def fetch(image, proxy):
    repository, tag = image.removeprefix("ghcr.io/").rsplit(":", 1)
    with httpx.Client(proxy=proxy, follow_redirects=True, timeout=180, trust_env=False) as client:
        def headers():
            response = client.get("https://ghcr.io/token", params={"service": "ghcr.io", "scope": f"repository:{repository}:pull"})
            response.raise_for_status()
            return {"Authorization": "Bearer " + response.json()["token"], "Accept": ACCEPT}

        def get(path):
            for attempt in range(6):
                try:
                    response = client.get(f"https://ghcr.io/v2/{repository}/{path}", headers=headers())
                    response.raise_for_status()
                    return response
                except httpx.HTTPError:
                    if attempt == 5:
                        raise
                    print(f"Retry metadata {image}: {attempt + 1}", flush=True)
                    time.sleep(2)

        response = get(f"manifests/{tag}")
        index_digest = "sha256:" + hashlib.sha256(response.content).hexdigest()
        remote_digest = response.headers.get("Docker-Content-Digest")
        if remote_digest:
            verified(response.content, remote_digest)
        manifest = response.json()
        if "manifests" in manifest:
            descriptor = next(item for item in manifest["manifests"]
                              if item.get("platform", {}).get("os") == "linux"
                              and item["platform"].get("architecture") == "amd64")
            response = get(f"manifests/{descriptor['digest']}")
            verified(response.content, descriptor["digest"])
            manifest = response.json()
        manifest_bytes = response.content
        manifest_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
        (CACHE / "blobs/sha256").mkdir(parents=True, exist_ok=True)
        (CACHE / "blobs/sha256" / manifest_digest.split(":")[1]).write_bytes(manifest_bytes)

        def blob(descriptor):
            digest = descriptor["digest"]
            destination = CACHE / "blobs/sha256" / digest.split(":")[1]
            if destination.exists() and destination.stat().st_size == descriptor["size"]:
                with destination.open("rb") as stream:
                    actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
                if actual == digest:
                    return
            partial = destination.with_suffix(".part")
            for attempt in range(8):
                try:
                    while not partial.exists() or partial.stat().st_size < descriptor["size"]:
                        offset = partial.stat().st_size if partial.exists() else 0
                        request_headers = headers()
                        end = min(offset + 32 * 1024 * 1024 - 1, descriptor["size"] - 1)
                        request_headers["Range"] = f"bytes={offset}-{end}"
                        with client.stream("GET", f"https://ghcr.io/v2/{repository}/blobs/{digest}", headers=request_headers) as download:
                            download.raise_for_status()
                            append = download.status_code == 206 and offset > 0
                            with partial.open("ab" if append else "wb") as output:
                                for chunk in download.iter_bytes(65536):
                                    output.write(chunk)
                        if descriptor["size"] > 32 * 1024 * 1024:
                            print(f"Resumed {image}: {digest[:19]} {partial.stat().st_size / 1048576:.1f}/{descriptor['size'] / 1048576:.1f} MiB", flush=True)
                    with partial.open("rb") as stream:
                        actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
                    if actual != digest or partial.stat().st_size != descriptor["size"]:
                        raise ValueError("Layer digest or size mismatch")
                    partial.replace(destination)
                    print(f"Verified {image}: {digest[:19]} ({descriptor['size'] / 1048576:.1f} MiB)", flush=True)
                    return
                except httpx.HTTPError:
                    if attempt == 7:
                        raise
                    print(f"Retry layer {image}: {digest[:19]}", flush=True)
                    time.sleep(2)

        def locked_blob(descriptor):
            with LOCKS_GUARD:
                lock = LOCKS.setdefault(descriptor["digest"], threading.Lock())
            with lock:
                blob(descriptor)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(locked_blob, [manifest["config"], *manifest["layers"]]))
        config = json.loads((CACHE / "blobs/sha256" / manifest["config"]["digest"].split(":")[1]).read_bytes())
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise ValueError("Unexpected image platform")
        index = {"schemaVersion": 2, "manifests": [{
            "mediaType": manifest["mediaType"], "digest": manifest_digest, "size": len(manifest_bytes),
            "annotations": {"io.containerd.image.name": image, "org.opencontainers.image.ref.name": image},
            "platform": {"os": "linux", "architecture": "amd64"},
        }]}
        archive = CACHE / (tag + ".tar")
        with tarfile.open(archive, "w") as tar:
            for name, payload in {"oci-layout": {"imageLayoutVersion": "1.0.0"}, "index.json": index}.items():
                encoded = json.dumps(payload).encode()
                item = tarfile.TarInfo(name)
                item.size = len(encoded)
                tar.addfile(item, io.BytesIO(encoded))
            digests = [manifest_digest, manifest["config"]["digest"], *[layer["digest"] for layer in manifest["layers"]]]
            for digest in dict.fromkeys(digests):
                name = "blobs/sha256/" + digest.split(":")[1]
                tar.add(CACHE / name, arcname=name)
        subprocess.run([DOCKER, "load", "-i", str(archive)], check=True)
        result = subprocess.run([DOCKER, "image", "inspect", image, "--format", "{{json .}}"], capture_output=True, text=True, check=True)
        inspected = json.loads(result.stdout)
        if (inspected.get("Descriptor", {}).get("digest") != manifest_digest
                or inspected["RootFS"]["Layers"] != config["rootfs"]["diff_ids"]):
            raise ValueError("Loaded manifest or filesystem digest mismatch")
        evidence = {"image": image, "registry": "ghcr.io", "tag_index_digest": index_digest,
                    "platform_manifest_digest": manifest_digest, "manifest": manifest,
                    "config_digest": manifest["config"]["digest"], "loaded_image_id": inspected["Id"], "verified": True}
        evidence_path = ROOT / "artifacts/otel_demo/live_acceptance/local_windows/images"
        evidence_path.mkdir(parents=True, exist_ok=True)
        (evidence_path / (tag + ".json")).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"Loaded verified official image: {image}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("images", nargs="+")
    parser.add_argument("--proxy", default="http://127.0.0.1:7897")
    parser.add_argument("--jobs", type=int, default=3, choices=range(1, 5))
    args = parser.parse_args()

    def process(image):
        if not image.startswith("ghcr.io/"):
            raise ValueError("Only official GHCR references are supported")
        exists = subprocess.run([DOCKER, "image", "inspect", image], capture_output=True, check=False).returncode == 0
        evidence_file = ROOT / "artifacts/otel_demo/live_acceptance/local_windows/images" / (image.rsplit(":", 1)[1] + ".json")
        if not exists or not evidence_file.exists():
            try:
                fetch(image, args.proxy)
            except (httpx.HTTPError, subprocess.SubprocessError, OSError, ValueError, KeyError, StopIteration) as exc:
                print(f"FAILED {image}: {type(exc).__name__}", flush=True)
                return False
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as image_pool:
        outcomes = list(image_pool.map(process, args.images))
    raise SystemExit(0 if all(outcomes) else 1)
