#!/usr/bin/env python3
"""Independent two-build reproducibility proof for the maops-docker-platform
release image.

SCOPE: this proves that two clean, independent, `--no-cache` BuildKit
builds from the identical source tree and identical build inputs produce
the *same application image identity* - not merely "the app behaves the
same." See docs/build-security.md for the full deterministic-build
strategy and evidence-level discussion.

Strategy (empirically verified against this project's actual Docker
Desktop/BuildKit v0.32.2 install before being adopted here - see
docs/build-security.md for what was tried and why):

  docker buildx build --no-cache \
      --build-arg VERSION=<VERSION> \
      --build-arg SOURCE_DATE_EPOCH=<fixed epoch, see below> \
      --output type=docker,rewrite-timestamp=true,name=<tag>,dest=<tar> \
      -f docker/app/Dockerfile .
  docker load -i <tar>

`--output type=docker,rewrite-timestamp=true` is BuildKit's own
reproducible-builds export mode: it normalizes every layer's file
timestamps and the image config's `Created` field to `SOURCE_DATE_EPOCH`
at export time, rather than leaving them at the real wall-clock instant
each `RUN`/`COPY` instruction executed - which is genuinely nondeterministic
between two separate build runs (confirmed by this project's own probing:
without `rewrite-timestamp=true`, the `RUN groupadd/useradd` layer's
`/etc/passwd`/`/etc/group`/etc. mtimes differed by several real seconds
between two immediately-sequential builds, changing that layer's diff ID
and therefore the final image ID). A plain `docker build`/`docker buildx
build --output type=docker` (the default, without a container-driver
`dest=`+`docker load` round-trip) does not accept `rewrite-timestamp=true`
against the `docker`-driver builder this project's Docker Desktop install
uses (`exporter option "rewrite-timestamp" conflicts with "unpack"`) - the
tar+`docker load` round-trip above is what actually works here, and is
what `make build` and this script both use.

`SOURCE_DATE_EPOCH` is the current commit's own timestamp
(`git log -1 --format=%ct`, deterministic per commit, never the current
wall clock) - not embedded as an image label anywhere, used only to
normalize otherwise-nondeterministic file/layer timestamps. Falls back to
a fixed sentinel (0) if no git history is available, never to
`time.time()`.

Independent evidence gathered, in addition to the exact image-ID
comparison: RootFS layer diff IDs, the full `Config` (labels, entrypoint,
cmd, user, healthcheck, exposed ports, env), and a normalized
content-addressed filesystem manifest of `/app` (path/type/mode/uid/gid/
symlink-target/sha256 content hash - deliberately excluding mtime, which
is exactly the axis `rewrite-timestamp=true` is normalizing) extracted
from a live container of each build via a small in-container Python walk
(no extra tooling installed). This directly proves file-content
equivalence from a code path independent of trusting the image-ID hash
alone.

Uses two uniquely tagged, disposable images (`maops-repro-<uuid>-a`/`-b`)
and disposable temp tar files/containers - never the real
`maops-docker-platform:<VERSION>` release image - and removes everything
it created, on success or failure. Never runs a global prune.

Day 4 (v0.4.0): `docker/app/Dockerfile` is now a two-stage build (a
digest-pinned `python:3.13-slim` builder that only prepares a filesystem
tree, and a digest-pinned Distroless final runtime with no shell). This
script's build invocation is unchanged - `docker buildx build -f
docker/app/Dockerfile .` already builds every stage and discards the
builder's own layers from the exported final image regardless of stage
count, so multi-stage support needed no new logic here. The one thing
that DID need to change is the in-container manifest walk: it now execs
the absolute `/usr/bin/python3.13` interpreter (never a bare `python3`
name), since the Distroless final runtime has no shell to perform PATH
resolution.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = REPO_ROOT / "docker" / "app" / "Dockerfile"
BUILD_TIMEOUT_SECONDS = 600.0
LOAD_TIMEOUT_SECONDS = 120.0
STARTUP_DEADLINE_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.5
# Absolute interpreter path (Day 4): the release image's final runtime is
# Distroless (no shell, no coreutils), so the in-container manifest walk
# execs this directly rather than a bare "python3" name.
PYTHON_BIN = "/usr/bin/python3.13"

# In-container manifest walker: stdlib-only (python3 is guaranteed present
# in this image), normalizes ordering, and deliberately excludes mtime -
# the one property `rewrite-timestamp=true` legitimately normalizes across
# builds rather than preserves as "real" content.
_MANIFEST_SCRIPT = r"""
import hashlib, json, os, stat as statmod
root = os.environ.get("MAOPS_MANIFEST_ROOT", "/app")
entries = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames.sort()
    for name in sorted(dirnames) + sorted(filenames):
        path = os.path.join(dirpath, name)
        st = os.lstat(path)
        mode = statmod.S_IMODE(st.st_mode)
        if statmod.S_ISLNK(st.st_mode):
            entries.append({"path": path, "type": "symlink", "mode": mode,
                             "uid": st.st_uid, "gid": st.st_gid, "target": os.readlink(path)})
        elif statmod.S_ISDIR(st.st_mode):
            entries.append({"path": path, "type": "dir", "mode": mode,
                             "uid": st.st_uid, "gid": st.st_gid})
        else:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            entries.append({"path": path, "type": "file", "mode": mode,
                             "uid": st.st_uid, "gid": st.st_gid, "sha256": h.hexdigest()})
entries.sort(key=lambda e: e["path"])
print(json.dumps(entries, sort_keys=True))
"""


class ReproducibilityError(RuntimeError):
    pass


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout, check=False)


def compute_source_date_epoch() -> int:
    result = run(["git", "log", "-1", "--format=%ct"], timeout=10.0)
    if result.returncode == 0 and result.stdout.strip():
        return int(result.stdout.strip())
    return 0


def buildkit_reproducible_build(tag: str, dest_tar: Path, version: str, epoch: int) -> None:
    result = run(
        [
            "docker", "buildx", "build", "--no-cache",
            "--build-arg", f"VERSION={version}",
            "--build-arg", f"SOURCE_DATE_EPOCH={epoch}",
            "--output", f"type=docker,rewrite-timestamp=true,name={tag},dest={dest_tar}",
            "-f", str(DOCKERFILE), ".",
        ],
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise ReproducibilityError(f"buildx build failed for {tag}:\n{result.stderr.strip()[-4000:]}")


def docker_load(dest_tar: Path) -> None:
    result = subprocess.run(
        ["docker", "load", "-i", str(dest_tar)], capture_output=True, text=True, timeout=LOAD_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        raise ReproducibilityError(f"docker load failed for {dest_tar}: {result.stderr.strip()}")


def docker_json(args: list[str]) -> object:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=20.0, check=False)
    if result.returncode != 0:
        raise ReproducibilityError(f"docker {' '.join(args)} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def get_image_id(tag: str) -> str:
    return docker_json(["image", "inspect", tag, "--format", "{{json .Id}}"])


def get_rootfs(tag: str) -> object:
    return docker_json(["image", "inspect", tag, "--format", "{{json .RootFS}}"])


def get_config(tag: str) -> object:
    return docker_json(["image", "inspect", tag, "--format", "{{json .Config}}"])


def start_container(tag: str, container_name: str) -> None:
    result = subprocess.run(
        ["docker", "run", "-d", "--name", container_name, tag],
        capture_output=True, text=True, timeout=20.0, check=False,
    )
    if result.returncode != 0:
        raise ReproducibilityError(f"docker run failed for {tag}: {result.stderr.strip()}")


def wait_until_running(container_name: str, deadline_seconds: float) -> None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "inspect", container_name, "--format", "{{.State.Running}}"],
            capture_output=True, text=True, timeout=10.0, check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise ReproducibilityError(f"container {container_name} did not reach running state in time")


def extract_filesystem_manifest(container_name: str) -> list[dict]:
    result = subprocess.run(
        ["docker", "exec", container_name, PYTHON_BIN, "-c", _MANIFEST_SCRIPT],
        capture_output=True, text=True, timeout=60.0, check=False,
    )
    if result.returncode != 0:
        raise ReproducibilityError(f"manifest extraction failed in {container_name}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def cleanup_container(container_name: str) -> None:
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=20.0, check=False)


def cleanup_image(tag: str) -> None:
    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, text=True, timeout=20.0, check=False)


def main() -> int:
    if shutil.which("docker") is None:
        print("reproducibility_check: docker CLI not found on PATH", file=sys.stderr)
        return 1

    version = read_version()
    epoch = compute_source_date_epoch()
    run_id = uuid.uuid4().hex[:12]
    tag_a = f"maops-repro-{run_id}-a:{version}"
    tag_b = f"maops-repro-{run_id}-b:{version}"

    print(f"reproducibility_check: VERSION={version} SOURCE_DATE_EPOCH={epoch}")
    print(f"reproducibility_check: build A -> {tag_a}")
    print(f"reproducibility_check: build B -> {tag_b}")

    created_tags: list[str] = []
    created_containers: list[str] = []

    with tempfile.TemporaryDirectory(prefix="maops-repro-") as tmp_dir:
        tar_a = Path(tmp_dir) / "a.tar"
        tar_b = Path(tmp_dir) / "b.tar"

        try:
            buildkit_reproducible_build(tag_a, tar_a, version, epoch)
            docker_load(tar_a)
            created_tags.append(tag_a)

            buildkit_reproducible_build(tag_b, tar_b, version, epoch)
            docker_load(tar_b)
            created_tags.append(tag_b)

            image_id_a = get_image_id(tag_a)
            image_id_b = get_image_id(tag_b)
            exact_match = image_id_a == image_id_b
            print(f"reproducibility_check: image ID A = {image_id_a}")
            print(f"reproducibility_check: image ID B = {image_id_b}")

            rootfs_a = get_rootfs(tag_a)
            rootfs_b = get_rootfs(tag_b)
            rootfs_match = rootfs_a == rootfs_b

            config_a = get_config(tag_a)
            config_b = get_config(tag_b)
            config_match = config_a == config_b

            container_a = f"maops-repro-{run_id}-a"
            container_b = f"maops-repro-{run_id}-b"
            start_container(tag_a, container_a)
            created_containers.append(container_a)
            wait_until_running(container_a, STARTUP_DEADLINE_SECONDS)
            manifest_a = extract_filesystem_manifest(container_a)

            start_container(tag_b, container_b)
            created_containers.append(container_b)
            wait_until_running(container_b, STARTUP_DEADLINE_SECONDS)
            manifest_b = extract_filesystem_manifest(container_b)

            manifest_match = manifest_a == manifest_b

            print()
            print(f"reproducibility_check: exact image ID equality:   {'PASS' if exact_match else 'FAIL'}")
            print(f"reproducibility_check: RootFS diff-ID equality:   {'PASS' if rootfs_match else 'FAIL'}")
            print(f"reproducibility_check: Config/OCI-label equality: {'PASS' if config_match else 'FAIL'}")
            print(
                f"reproducibility_check: normalized filesystem manifest "
                f"({len(manifest_a)} entries): {'PASS' if manifest_match else 'FAIL'}"
            )

            all_passed = exact_match and rootfs_match and config_match and manifest_match
            print()
            if not all_passed:
                if not manifest_match:
                    for entry_a, entry_b in zip(manifest_a, manifest_b):
                        if entry_a != entry_b:
                            print(f"reproducibility_check: first manifest divergence: A={entry_a} B={entry_b}", file=sys.stderr)
                            break
                print("reproducibility_check: FAIL - build A and build B are not reproducible", file=sys.stderr)
                return 1

            print(
                "reproducibility_check: PASS - STRONG evidence level: exact image ID "
                "equality (build A == build B), independently corroborated by RootFS "
                "diff-ID equality, Config/OCI-label equality, and a normalized "
                "content-addressed filesystem manifest of /app."
            )
            return 0

        finally:
            for container_name in created_containers:
                cleanup_container(container_name)
            for tag in created_tags:
                cleanup_image(tag)


if __name__ == "__main__":
    sys.exit(main())
