#!/usr/bin/env python3
"""Run the complete static k-core experiment suite on a larger computer.

The runner supports Linux and macOS, captures machine/JVM metadata and peak RSS,
downloads and normalizes graphs, generates degree-based query sets without
NetworkX, runs every static Java experiment sequentially, validates outputs,
creates TCP-included and TCP-omitted figures, and writes a compact Git-ready
bundle under remote-results/<run-id>/.

No command runs unless --execute is supplied. Interrupted runs can be resumed by
reusing --run-id with --resume.

Examples:
    # First inspect the plan and resource estimate.
    python3 tools/run_large_experiments.py --datasets all --heap-gb 512

    # Run everything on a large Linux server.
    python3 tools/run_large_experiments.py --execute --datasets all \
        --heap-gb 512 --run-id server-full-1 \
        --archive uk2002=/path/to/uk-2002.tar.gz --install-dependencies

    # Resume after interruption with the same immutable configuration.
    python3 tools/run_large_experiments.py --execute --datasets all \
        --heap-gb 512 --run-id server-full-1 --resume \
        --archive uk2002=/path/to/uk-2002.tar.gz --install-dependencies

    # UK-2002 requires a locally downloaded Matrix Market archive.
    python3 tools/run_large_experiments.py --execute --datasets uk2002 \
        --heap-gb 512 --run-id uk-full-1 \
        --archive uk2002=/path/to/uk-2002.tar.gz --install-dependencies
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import platform
import random
import re
import secrets
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tarfile
import time
import urllib.request
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence


@dataclass(frozen=True)
class Dataset:
    key: str
    title: str
    url: str
    archive_kind: str
    expected_vertices: int
    expected_edges: int | None
    fixed_k: int
    sweep_k: tuple[int, ...]
    minimum_ram_gib: int
    source_page: str


DATASETS = {
    "grqc": Dataset(
        "grqc", "GrQc", "https://snap.stanford.edu/data/ca-GrQc.txt.gz", "snap-gzip",
        5_241, 14_484, 5, (2, 3, 5, 8, 12, 20, 30, 43), 8,
        "https://snap.stanford.edu/data/ca-GrQc.html",
    ),
    "amazon": Dataset(
        "amazon", "Amazon", "https://snap.stanford.edu/data/bigdata/communities/com-amazon.ungraph.txt.gz",
        "snap-gzip", 334_863, 925_872, 4, (2, 3, 4, 5, 6), 16,
        "https://snap.stanford.edu/data/com-Amazon.html",
    ),
    "dblp": Dataset(
        "dblp", "DBLP", "https://snap.stanford.edu/data/bigdata/communities/com-dblp.ungraph.txt.gz",
        "snap-gzip", 317_080, 1_049_866, 5, (2, 5, 10, 20), 16,
        "https://snap.stanford.edu/data/com-DBLP.html",
    ),
    "livejournal": Dataset(
        "livejournal", "LiveJournal",
        "https://snap.stanford.edu/data/bigdata/communities/com-lj.ungraph.txt.gz",
        "snap-gzip", 3_997_962, 34_681_189, 6, (2, 5, 6, 7, 8), 128,
        "https://snap.stanford.edu/data/com-LiveJournal.html",
    ),
    "orkut": Dataset(
        "orkut", "Orkut",
        "https://snap.stanford.edu/data/bigdata/communities/com-orkut.ungraph.txt.gz",
        "snap-gzip", 3_072_441, 117_185_083, 10, (2, 5, 6, 7, 8, 9, 10), 384,
        "https://snap.stanford.edu/data/com-Orkut.html",
    ),
    "uk2002": Dataset(
        "uk2002", "UK-2002",
        "https://sparse.tamu.edu/LAW/uk-2002",
        "matrix-market-tar", 18_520_486, None, 10, (2, 5, 6, 7, 8, 9, 10), 768,
        "https://sparse.tamu.edu/LAW/uk-2002",
    ),
}

TRACKED_QUERIES = {
    "grqc": ("data/grqc_buckets.txt", "data/grqc_highlow.txt"),
    "amazon": ("data/amazon_buckets.txt", "data/amazon_highlow.txt"),
    "dblp": ("data/dblp_buckets_100.txt", "data/dblp_highlow_100.txt"),
}

MINIMUM_DISK_GIB = {
    "grqc": 1,
    "amazon": 2,
    "dblp": 3,
    "livejournal": 30,
    "orkut": 100,
    "uk2002": 200,
}

class RunnerError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capture(argv: Sequence[str], cwd: Path | None = None) -> dict[str, object]:
    try:
        completed = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=60, check=False)
        return {
            "argv": list(argv),
            "exit_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"argv": list(argv), "error": str(error)}


def public_capture(argv: Sequence[str], cwd: Path | None = None) -> dict[str, object]:
    result = capture(argv, cwd)
    result.pop("argv", None)
    return result


def public_runner_arguments(args: argparse.Namespace) -> dict[str, object]:
    values = vars(args).copy()
    values["archive"] = [
        f"{item.split('=', 1)[0]}={Path(item.split('=', 1)[1]).name}" if "=" in item else item
        for item in values.get("archive", [])
    ]
    values["java_home_supplied"] = bool(values.pop("java_home", None))
    return values


def physical_memory_bytes() -> int | None:
    if sys.platform == "darwin":
        result = capture(["sysctl", "-n", "hw.memsize"])
        try:
            return int(str(result.get("stdout", "")))
        except ValueError:
            return None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo.read_text(), re.MULTILINE)
        if match:
            return int(match.group(1)) * 1024
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None


def available_memory_bytes() -> int | None:
    if sys.platform == "darwin":
        result = capture(["vm_stat"])
        text = str(result.get("stdout", ""))
        page_match = re.search(r"page size of (\d+) bytes", text)
        if not page_match:
            return None
        page_size = int(page_match.group(1))
        page_total = 0
        for label in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
            match = re.search(rf"^{re.escape(label)}:\s+(\d+)\.", text, re.MULTILINE)
            if match:
                page_total += int(match.group(1))
        return page_total * page_size
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        match = re.search(r"^MemAvailable:\s+(\d+)\s+kB", meminfo.read_text(), re.MULTILINE)
        if match:
            return int(match.group(1)) * 1024
    return None


def swap_information() -> dict[str, object]:
    if sys.platform == "darwin":
        return capture(["sysctl", "vm.swapusage"])
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        text = meminfo.read_text()
        total = re.search(r"^SwapTotal:\s+(\d+)\s+kB", text, re.MULTILINE)
        free = re.search(r"^SwapFree:\s+(\d+)\s+kB", text, re.MULTILINE)
        return {
            "total_bytes": int(total.group(1)) * 1024 if total else None,
            "free_bytes": int(free.group(1)) * 1024 if free else None,
        }
    return {}


def cpu_model() -> str | None:
    if sys.platform == "darwin":
        result = capture(["sysctl", "-n", "machdep.cpu.brand_string"])
        return str(result.get("stdout")) if result.get("stdout") else None
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        match = re.search(r"^(?:model name|Hardware)\s*:\s*(.+)$", cpuinfo.read_text(errors="replace"), re.MULTILINE)
        if match:
            return match.group(1).strip()
    return platform.processor() or None


def parse_peak_rss(stderr_path: Path) -> int | None:
    text = stderr_path.read_text(errors="replace") if stderr_path.exists() else ""
    if sys.platform == "darwin":
        match = re.search(r"(\d+)\s+maximum resident set size", text)
        return int(match.group(1)) if match else None
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", text)
    return int(match.group(1)) * 1024 if match else None


def terminate_process_group(process: subprocess.Popen[object]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def parse_archive_overrides(values: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise RunnerError(f"invalid --archive value {value!r}; expected dataset=/path")
        key, raw_path = value.split("=", 1)
        if key not in DATASETS:
            raise RunnerError(f"unknown dataset in --archive: {key}")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise RunnerError(f"archive does not exist: {path}")
        result[key] = path
    return result


def parse_datasets(value: str) -> list[Dataset]:
    keys = list(DATASETS) if value == "all" else [item.strip() for item in value.split(",") if item.strip()]
    unknown = [key for key in keys if key not in DATASETS]
    if unknown:
        raise RunnerError(f"unknown datasets: {', '.join(unknown)}")
    return [DATASETS[key] for key in keys]


def resolve_java(explicit_home: str | None) -> tuple[str, str]:
    def valid(java_path: Path, javac_path: Path) -> bool:
        if not java_path.is_file() or not javac_path.is_file():
            return False
        java_probe = capture([str(java_path), "-version"])
        javac_probe = capture([str(javac_path), "-version"])
        return java_probe.get("exit_code") == 0 and javac_probe.get("exit_code") == 0

    homes = [explicit_home, os.environ.get("JAVA_HOME")]
    if sys.platform == "darwin":
        homes.extend(["/opt/homebrew/opt/openjdk", "/usr/local/opt/openjdk"])
        java_home_probe = capture(["/usr/libexec/java_home"])
        if java_home_probe.get("exit_code") == 0 and java_home_probe.get("stdout"):
            homes.append(str(java_home_probe["stdout"]).strip())
    for raw_home in homes:
        if raw_home:
            home = Path(raw_home).expanduser()
            java_path = home / "bin" / "java"
            javac_path = home / "bin" / "javac"
            if valid(java_path, javac_path):
                return str(java_path), str(javac_path)

    java_raw = shutil.which("java")
    javac_raw = shutil.which("javac")
    if java_raw and javac_raw:
        java_path = Path(java_raw)
        javac_path = Path(javac_raw)
        if valid(java_path, javac_path):
            return str(java_path), str(javac_path)
    raise RunnerError("working JDK not found; pass --java-home or set JAVA_HOME")


def system_information(repo: Path, java: str, javac: str, args: argparse.Namespace) -> dict[str, object]:
    disk = shutil.disk_usage(repo)
    memory = physical_memory_bytes()
    available_memory = available_memory_bytes()
    return {
        "captured_at_utc": utc_now(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "architecture": platform.machine(),
        "cpu_model": cpu_model(),
        "logical_cpu_count": os.cpu_count(),
        "physical_memory_bytes": memory,
        "physical_memory_gib": round(memory / 2**30, 3) if memory else None,
        "available_memory_bytes": available_memory,
        "available_memory_gib": round(available_memory / 2**30, 3) if available_memory else None,
        "swap": swap_information(),
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "disk_free_gib": round(disk.free / 2**30, 3),
        "python": public_capture([sys.executable, "--version"]),
        "java": public_capture([java, "-version"]),
        "javac": public_capture([javac, "-version"]),
        "git_commit": public_capture(["git", "rev-parse", "HEAD"], repo),
        "git_branch": public_capture(["git", "branch", "--show-current"], repo),
        "git_status": public_capture(["git", "status", "--porcelain=v1"], repo),
        "host_commands": {
            "lscpu": capture(["lscpu"]) if shutil.which("lscpu") else None,
            "free": capture(["free", "-b"]) if shutil.which("free") else None,
            "sw_vers": capture(["sw_vers"]) if shutil.which("sw_vers") else None,
        },
        "timezone": time.tzname,
        "locale": {"LANG": os.environ.get("LANG"), "LC_ALL": os.environ.get("LC_ALL")},
        "runner_arguments": public_runner_arguments(args),
    }


class RunManager:
    def __init__(self, repo: Path, run_id: str, resume: bool, timeout_hours: float):
        self.repo = repo
        self.run_id = run_id
        runs_root = (repo / "runs").resolve()
        publish_root = (repo / "remote-results").resolve()
        self.run_dir = (runs_root / run_id).resolve()
        self.publish_dir = (publish_root / run_id).resolve()
        if not self.run_dir.is_relative_to(runs_root) or not self.publish_dir.is_relative_to(publish_root):
            raise RunnerError("run directory escapes its managed root")
        self.tasks_dir = self.run_dir / "tasks"
        self.logs_dir = self.run_dir / "logs"
        self.resume = resume
        self.timeout = timeout_hours * 3600 if timeout_hours else None
        self.hash_cache_path = self.run_dir / "meta" / "hash-cache.json"
        self.runner_hash = sha256(Path(__file__).resolve())
        self.hash_cache: dict[str, dict[str, object]] = {}
        if self.hash_cache_path.exists():
            self.hash_cache = json.loads(self.hash_cache_path.read_text())

    def initialize(self) -> None:
        if self.run_dir.exists() and not self.resume:
            raise RunnerError(f"run already exists: {self.run_dir}; use --resume or a new --run-id")
        if self.publish_dir.exists() and any(self.publish_dir.iterdir()) and not self.resume:
            raise RunnerError(f"publish bundle already exists: {self.publish_dir}; use a new --run-id")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.publish_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.acquire_lock()

    def acquire_lock(self) -> None:
        lock = self.run_dir / "run.lock"
        if lock.exists():
            try:
                old = json.loads(lock.read_text())
            except json.JSONDecodeError:
                old = {}
            if old.get("hostname") == socket.gethostname() and isinstance(old.get("pid"), int):
                try:
                    os.kill(old["pid"], 0)
                    raise RunnerError(f"run is already active under PID {old['pid']}")
                except ProcessLookupError:
                    pass
                except PermissionError:
                    raise RunnerError(f"cannot verify existing run PID {old['pid']}")
            lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as handle:
            json.dump({"pid": os.getpid(), "hostname": socket.gethostname(), "started_at": utc_now()}, handle)

    def release_lock(self) -> None:
        lock = self.run_dir / "run.lock"
        if lock.exists():
            lock.unlink()

    def file_hash(self, path: Path) -> str:
        path = path.resolve()
        stat = path.stat()
        key = str(path)
        cached = self.hash_cache.get(key)
        if cached and cached.get("size") == stat.st_size and cached.get("mtime_ns") == stat.st_mtime_ns:
            return str(cached["sha256"])
        value = sha256(path)
        self.hash_cache[key] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": value}
        atomic_json(self.hash_cache_path, self.hash_cache)
        return value

    def signature(self, descriptor: object, inputs: Sequence[Path]) -> tuple[str, dict[str, str]]:
        input_hashes = {str(path.resolve()): self.file_hash(path) for path in inputs}
        encoded = json.dumps(
            {"descriptor": descriptor, "inputs": input_hashes, "runner_sha256": self.runner_hash},
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest(), input_hashes

    def state_path(self, task_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id)
        return self.tasks_dir / f"{safe}.json"

    def can_resume(self, task_id: str, signature: str, outputs: Sequence[Path]) -> bool:
        state_path = self.state_path(task_id)
        if not self.resume or not state_path.exists():
            return False
        try:
            state = json.loads(state_path.read_text())
        except json.JSONDecodeError:
            return False
        if state.get("status") != "complete" or state.get("signature") != signature:
            return False
        recorded = state.get("outputs", {})
        for output in outputs:
            key = str(output.resolve())
            if not output.is_file() or key not in recorded or self.file_hash(output) != recorded[key]["sha256"]:
                return False
        print(f"[resume] {task_id}")
        return True

    def run_action(
        self,
        task_id: str,
        descriptor: object,
        inputs: Sequence[Path],
        outputs: Sequence[Path],
        action: Callable[[], None],
        validator: Callable[[], None] | None = None,
    ) -> None:
        signature, input_hashes = self.signature(descriptor, inputs)
        if self.can_resume(task_id, signature, outputs):
            return
        state_path = self.state_path(task_id)
        started = utc_now()
        atomic_json(state_path, {
            "task": task_id, "status": "running", "signature": signature,
            "inputs": input_hashes, "started_at": started,
        })
        print(f"[run] {task_id}")
        started_monotonic = time.monotonic()
        try:
            action()
            if validator:
                validator()
            output_data = {
                str(path.resolve()): {"sha256": self.file_hash(path), "bytes": path.stat().st_size}
                for path in outputs
            }
            atomic_json(state_path, {
                "task": task_id, "status": "complete", "signature": signature,
                "inputs": input_hashes, "outputs": output_data,
                "started_at": started, "completed_at": utc_now(),
                "elapsed_seconds": time.monotonic() - started_monotonic,
            })
        except BaseException as error:
            atomic_json(state_path, {
                "task": task_id, "status": "failed", "signature": signature,
                "inputs": input_hashes, "started_at": started,
                "failed_at": utc_now(), "error": repr(error),
            })
            raise

    def run_command(
        self,
        task_id: str,
        argv: Sequence[str],
        inputs: Sequence[Path],
        outputs: Sequence[Path],
        validator: Callable[[Path], None] | None = None,
    ) -> None:
        descriptor = {"argv": list(argv), "cwd": str(self.repo)}
        signature, input_hashes = self.signature(descriptor, inputs)
        log_dir = self.logs_dir / re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id)
        stdout_path = log_dir / "stdout.log"
        stderr_path = log_dir / "stderr-and-time.log"
        command_path = log_dir / "command.json"
        resume_outputs = [*outputs, stdout_path, stderr_path, command_path]
        if self.can_resume(task_id, signature, resume_outputs):
            return

        log_dir.mkdir(parents=True, exist_ok=True)
        for output in outputs:
            if output.exists():
                resolved = output.resolve()
                managed_roots = (self.run_dir.resolve(), self.publish_dir.resolve())
                if not any(resolved.is_relative_to(root) for root in managed_roots):
                    raise RunnerError(f"refusing to replace unmanaged output: {output}")
                if output.is_file() or output.is_symlink():
                    output.unlink()
                else:
                    raise RunnerError(f"declared command output is not a file: {output}")
        state_path = self.state_path(task_id)
        started_at = utc_now()

        time_binary = Path("/usr/bin/time")
        if time_binary.exists():
            wrapped = [str(time_binary), "-l" if sys.platform == "darwin" else "-v", *argv]
        else:
            wrapped = list(argv)
        atomic_json(command_path, {"argv": list(argv), "wrapped_argv": wrapped, "started_at": started_at})
        atomic_json(state_path, {
            "task": task_id, "status": "running", "signature": signature,
            "inputs": input_hashes, "started_at": started_at,
        })
        print(f"[run] {task_id}: {' '.join(argv)}")

        started_monotonic = time.monotonic()
        try:
            with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
                process = subprocess.Popen(
                    wrapped,
                    cwd=self.repo,
                    stdout=stdout,
                    stderr=stderr,
                    env={**os.environ, "LC_ALL": "C", "LANG": "C", "TZ": "UTC"},
                    start_new_session=True,
                )
                try:
                    return_code = process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired as error:
                    terminate_process_group(process)
                    raise RunnerError(f"task {task_id} exceeded its timeout; process group terminated") from error
                except KeyboardInterrupt:
                    terminate_process_group(process)
                    raise
            elapsed = time.monotonic() - started_monotonic
            if return_code != 0:
                raise RunnerError(f"task {task_id} exited {return_code}; see {stderr_path}")
            if validator:
                validator(stdout_path)
            for output in outputs:
                if not output.is_file() or output.stat().st_size == 0:
                    raise RunnerError(f"task {task_id} did not create {output}")
            output_data = {
                str(path.resolve()): {"sha256": self.file_hash(path), "bytes": path.stat().st_size}
                for path in resume_outputs
            }
            peak_rss = parse_peak_rss(stderr_path)
            atomic_json(state_path, {
                "task": task_id, "status": "complete", "signature": signature,
                "inputs": input_hashes, "outputs": output_data,
                "started_at": started_at, "completed_at": utc_now(),
                "elapsed_seconds": elapsed, "peak_rss_bytes": peak_rss,
                "stdout": str(stdout_path), "stderr_and_time": str(stderr_path),
            })
        except BaseException as error:
            atomic_json(state_path, {
                "task": task_id, "status": "failed", "signature": signature,
                "inputs": input_hashes, "started_at": started_at,
                "failed_at": utc_now(), "error": repr(error),
                "stdout": str(stdout_path), "stderr_and_time": str(stderr_path),
            })
            raise


class SecureRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not newurl.lower().startswith("https://"):
            raise RunnerError(f"refusing non-HTTPS redirect to {newurl}")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    existing = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "generic-decomposition-index-experiment-runner/1"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    opener = urllib.request.build_opener(SecureRedirectHandler())
    response = opener.open(urllib.request.Request(url, headers=headers), timeout=120)
    if existing and getattr(response, "status", None) != 206:
        partial.unlink()
        existing = 0
        response.close()
        response = opener.open(urllib.request.Request(url, headers={"User-Agent": headers["User-Agent"]}), timeout=120)
    mode = "ab" if existing else "wb"
    written = existing
    next_report = ((written // 2**30) + 1) * 2**30
    with response, partial.open(mode) as output:
        while True:
            block = response.read(8 * 1024 * 1024)
            if not block:
                break
            output.write(block)
            written += len(block)
            if written >= next_report:
                print(f"  downloaded {written / 2**30:.1f} GiB")
                next_report += 2**30
    if written == 0:
        raise RunnerError(f"empty download from {url}")
    os.replace(partial, destination)


def iter_snap_edges(archive: Path) -> Iterator[tuple[int, int]]:
    with gzip.open(archive, "rt", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) < 2:
                raise RunnerError(f"invalid edge at {archive}:{line_number}")
            yield int(parts[0]), int(parts[1])


def iter_matrix_market_edges(archive: Path) -> Iterator[tuple[int, int]]:
    expected_rows = 18_520_486
    expected_columns = 18_520_486
    expected_entries = 298_113_762
    with tarfile.open(archive, "r:*") as container:
        members = [member for member in container.getmembers() if member.isfile() and member.name.endswith(".mtx")]
        if len(members) != 1:
            raise RunnerError(f"expected one .mtx file in {archive}, found {len(members)}")
        extracted = container.extractfile(members[0])
        if extracted is None:
            raise RunnerError(f"cannot read {members[0].name}")

        header = extracted.readline().decode("ascii", errors="strict").strip().lower()
        if header != "%%matrixmarket matrix coordinate pattern general":
            raise RunnerError(f"unexpected UK-2002 Matrix Market header: {header}")

        dimensions = None
        for raw in extracted:
            line = raw.decode("ascii", errors="strict").strip()
            if not line or line.startswith("%"):
                continue
            parts = line.split()
            if len(parts) != 3:
                raise RunnerError("invalid Matrix Market dimensions")
            dimensions = tuple(map(int, parts))
            break
        if dimensions != (expected_rows, expected_columns, expected_entries):
            raise RunnerError(
                f"unexpected UK-2002 dimensions {dimensions}; expected "
                f"{(expected_rows, expected_columns, expected_entries)}"
            )

        entries = 0
        for raw in extracted:
            line = raw.decode("ascii", errors="strict").strip()
            if not line or line.startswith("%"):
                continue
            parts = line.split()
            if len(parts) < 2:
                raise RunnerError("invalid Matrix Market coordinate")
            u, v = int(parts[0]), int(parts[1])
            if not (1 <= u <= expected_rows and 1 <= v <= expected_columns):
                raise RunnerError(f"UK-2002 coordinate out of bounds: {(u, v)}")
            entries += 1
            yield u, v
        if entries != expected_entries:
            raise RunnerError(f"UK-2002 declared {expected_entries} entries but contained {entries}")


def normalize_graph(dataset: Dataset, archive: Path, graph_path: Path, degree_path: Path, meta_path: Path, tmp: Path) -> None:
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    partial = graph_path.with_suffix(".el.partial")
    sort_stderr = tmp / "sort.stderr"
    edge_source = iter_snap_edges(archive) if dataset.archive_kind == "snap-gzip" else iter_matrix_market_edges(archive)
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "TMPDIR": str(tmp)}
    with partial.open("wb") as output, sort_stderr.open("wb") as error:
        process = subprocess.Popen(
            ["sort", "-n", "-k1,1", "-k2,2", "-u"], stdin=subprocess.PIPE, stdout=output, stderr=error, env=env
        )
        assert process.stdin is not None
        buffer = bytearray()
        raw_edges = 0
        try:
            for u, v in edge_source:
                raw_edges += 1
                if u == v:
                    continue
                if u > v:
                    u, v = v, u
                buffer.extend(f"{u}\t{v}\n".encode("ascii"))
                if len(buffer) >= 8 * 1024 * 1024:
                    process.stdin.write(buffer)
                    buffer.clear()
            if buffer:
                process.stdin.write(buffer)
            process.stdin.close()
            return_code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
    if return_code != 0:
        raise RunnerError(f"sort failed; see {sort_stderr}")
    os.replace(partial, graph_path)

    degrees = array("I")
    edge_count = 0
    with graph_path.open() as handle:
        for line in handle:
            u_text, v_text = line.split()
            u, v = int(u_text), int(v_text)
            maximum = max(u, v)
            if maximum >= len(degrees):
                degrees.extend(array("I", [0]) * (maximum + 1 - len(degrees)))
            degrees[u] += 1
            degrees[v] += 1
            edge_count += 1
    vertices = sum(1 for degree in degrees if degree)
    if dataset.key != "uk2002" and vertices != dataset.expected_vertices:
        raise RunnerError(f"{dataset.key}: expected {dataset.expected_vertices} vertices, found {vertices}")
    if dataset.key == "uk2002" and vertices != dataset.expected_vertices:
        print(
            f"  UK-2002 contains {dataset.expected_vertices} matrix rows but {vertices} non-isolated vertices; "
            "Java experiments use only represented vertices."
        )
    if dataset.expected_edges is not None and edge_count != dataset.expected_edges:
        raise RunnerError(f"{dataset.key}: expected {dataset.expected_edges} edges, found {edge_count}")
    with degree_path.open("wb") as handle:
        degrees.tofile(handle)
    atomic_json(meta_path, {
        "dataset": dataset.key,
        "source": dataset.url,
        "source_page": dataset.source_page,
        "raw_edge_rows": raw_edges,
        "vertices": vertices,
        "undirected_unique_edges": edge_count,
        "max_vertex_id": len(degrees) - 1,
        "graph_sha256": sha256(graph_path),
    })


def reservoir_add(reservoir: list[int], seen: int, value: int, capacity: int, rng: random.Random) -> None:
    if len(reservoir) < capacity:
        reservoir.append(value)
        return
    position = rng.randrange(seen)
    if position < capacity:
        reservoir[position] = value


def generate_queries(
    degree_path: Path,
    meta_path: Path,
    fixed_path: Path,
    highlow_path: Path,
    fixed_k: int,
    per_bucket: int,
    per_set: int,
    seed: int,
    tmp: Path,
) -> None:
    meta = json.loads(meta_path.read_text())
    maximum = int(meta["max_vertex_id"])
    degrees = array("I")
    with degree_path.open("rb") as handle:
        degrees.fromfile(handle, maximum + 1)
    vertex_count = sum(1 for value in degrees if value)

    tmp.mkdir(parents=True, exist_ok=True)
    degree_rows = tmp / "degree-rows.txt"
    ranked = tmp / "degree-ranked.txt"
    with degree_rows.open("w") as output:
        for vertex, degree in enumerate(degrees):
            if degree:
                output.write(f"{degree}\t{vertex}\n")
    with ranked.open("w") as output:
        completed = subprocess.run(
            ["sort", "-k1,1nr", "-k2,2n", str(degree_rows)],
            stdout=output,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "LC_ALL": "C", "LANG": "C", "TMPDIR": str(tmp)},
            check=False,
        )
    if completed.returncode != 0:
        raise RunnerError(f"degree sort failed: {completed.stderr}")
    degree_rows.unlink()

    bucket_samples = [[] for _ in range(10)]
    bucket_seen = [0] * 10
    bucket_rng = [random.Random(seed + index) for index in range(10)]
    high: list[int] = []
    low: list[int] = []
    high_seen = 0
    low_seen = 0
    high_rng = random.Random(seed + 10_000)
    low_rng = random.Random(seed + 20_000)
    high_boundary = 3 * vertex_count // 10

    with ranked.open() as handle:
        for rank, line in enumerate(handle):
            _, vertex_text = line.split()
            vertex = int(vertex_text)
            bucket = min(9, ((rank + 1) * 10 - 1) // vertex_count)
            bucket_seen[bucket] += 1
            reservoir_add(bucket_samples[bucket], bucket_seen[bucket], vertex, per_bucket, bucket_rng[bucket])
            if rank < high_boundary:
                high_seen += 1
                reservoir_add(high, high_seen, vertex, per_set, high_rng)
            else:
                low_seen += 1
                reservoir_add(low, low_seen, vertex, per_set, low_rng)
    ranked.unlink()

    if any(len(sample) != per_bucket for sample in bucket_samples):
        raise RunnerError("at least one degree bucket is too small")
    if len(high) != per_set or len(low) != per_set:
        raise RunnerError("high/low query pools are too small")

    fixed_path.parent.mkdir(parents=True, exist_ok=True)
    with fixed_path.open("w") as output:
        output.write(f"# streaming degree buckets, k={fixed_k}, {per_bucket} per bucket, seed={seed}\n")
        output.write(f"# graph: {vertex_count} vertices, {meta['undirected_unique_edges']} edges\n")
        output.write("# vertex,k,bucket\n")
        for index, sample in enumerate(bucket_samples):
            for vertex in sample:
                output.write(f"{vertex},{fixed_k},p{index * 10}_{(index + 1) * 10}\n")
    with highlow_path.open("w") as output:
        output.write(f"# streaming high/low degree sets, {per_set} per set, seed={seed}\n")
        output.write(f"# graph: {vertex_count} vertices, {meta['undirected_unique_edges']} edges\n")
        output.write("# vertex,set\n")
        for vertex in high:
            output.write(f"{vertex},H\n")
        for vertex in low:
            output.write(f"{vertex},L\n")


def validate_query_files(
    fixed_path: Path,
    highlow_path: Path,
    fixed_k: int,
    per_bucket: int,
    per_set: int,
) -> None:
    fixed_rows = []
    for line in fixed_path.read_text().splitlines():
        if line and not line.startswith("#"):
            vertex, k, bucket = re.split(r"[,\s]+", line.strip())[:3]
            fixed_rows.append((int(vertex), int(k), bucket))
    if len(fixed_rows) != 10 * per_bucket:
        raise RunnerError(f"{fixed_path}: unexpected fixed-query count {len(fixed_rows)}")
    if len(set(fixed_rows)) != len(fixed_rows):
        raise RunnerError(f"{fixed_path}: duplicate fixed-query keys")
    if {row[1] for row in fixed_rows} != {fixed_k}:
        raise RunnerError(f"{fixed_path}: unexpected k value")
    bucket_counts = {f"p{index * 10}_{(index + 1) * 10}": 0 for index in range(10)}
    for _, _, bucket in fixed_rows:
        if bucket not in bucket_counts:
            raise RunnerError(f"{fixed_path}: unexpected bucket {bucket}")
        bucket_counts[bucket] += 1
    if any(count != per_bucket for count in bucket_counts.values()):
        raise RunnerError(f"{fixed_path}: incorrect bucket sizes {bucket_counts}")

    sets: dict[str, list[int]] = {"H": [], "L": []}
    for line in highlow_path.read_text().splitlines():
        if line and not line.startswith("#"):
            vertex, label = re.split(r"[,\s]+", line.strip())[:2]
            if label not in sets:
                raise RunnerError(f"{highlow_path}: unexpected set {label}")
            sets[label].append(int(vertex))
    if any(len(values) != per_set for values in sets.values()):
        raise RunnerError(f"{highlow_path}: incorrect H/L sizes")
    if len(set(sets["H"])) != per_set or len(set(sets["L"])) != per_set:
        raise RunnerError(f"{highlow_path}: duplicate query vertices")
    if set(sets["H"]) & set(sets["L"]):
        raise RunnerError(f"{highlow_path}: H/L query sets overlap")


def validate_csv_rows(path: Path, expected_rows: int) -> None:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != expected_rows:
        raise RunnerError(f"{path}: expected {expected_rows} rows, found {len(rows)}")


def validate_fixed_csv(path: Path, expected_rows: int) -> None:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != expected_rows:
        raise RunnerError(f"{path}: expected {expected_rows} rows, found {len(rows)}")
    keys = [(row["bucket"], row["vertex"], row["k"]) for row in rows]
    if len(set(keys)) != len(keys):
        raise RunnerError(f"{path}: duplicate query keys")


def aggregate_fixed_no_tcp(inputs: Sequence[Path], output: Path) -> None:
    runs = []
    for path in inputs:
        with path.open(newline="") as handle:
            runs.append(list(csv.DictReader(handle)))
    if not runs:
        raise RunnerError("no fixed-query runs to aggregate")
    keys = [[(row["bucket"], row["vertex"], row["k"]) for row in rows] for rows in runs]
    if any(run_keys != keys[0] for run_keys in keys[1:]):
        raise RunnerError("fixed-query repetitions use different ordered queries")
    fields = ["bucket", "vertex", "k", "communities", "answer_edges", "indexed_ms", "indexfree_ms", "speedup"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for index, key in enumerate(keys[0]):
            rows = [run[index] for run in runs]
            if len({(row["communities"], row["answer_edges"]) for row in rows}) != 1:
                raise RunnerError(f"fixed-query answer mismatch across repetitions for {key}")
            indexed = statistics.median(float(row["indexed_ms"]) for row in rows)
            indexfree = statistics.median(float(row["indexfree_ms"]) for row in rows)
            writer.writerow({
                "bucket": key[0],
                "vertex": key[1],
                "k": key[2],
                "communities": rows[0]["communities"],
                "answer_edges": rows[0]["answer_edges"],
                "indexed_ms": f"{indexed:.6f}",
                "indexfree_ms": f"{indexfree:.6f}",
                "speedup": f"{indexfree / indexed:.2f}" if indexed else "0.00",
            })


def validate_sweep_csv(path: Path, expected_cells: int, expected_methods: set[str]) -> None:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != expected_cells * len(expected_methods):
        raise RunnerError(f"{path}: unexpected row count {len(rows)}")
    if {row["method"] for row in rows} != expected_methods:
        raise RunnerError(f"{path}: unexpected methods")


def require_agreement(stdout_path: Path) -> None:
    text = stdout_path.read_text(errors="replace")
    if "agreement: IDENTICAL" not in text:
        raise RunnerError(f"agreement check missing or failed in {stdout_path}")


def parse_metric_compare(logs: Sequence[Path], dataset: Dataset, output: Path) -> None:
    records: dict[str, dict[str, list[float]]] = {
        "truss": {name: [] for name in ("decomposition_s", "index_s", "total_s")},
        "core": {name: [] for name in ("decomposition_s", "index_s", "total_s")},
    }
    static: dict[str, tuple[str, str]] = {}
    labels = {
        "decomposition (s)": "decomposition_s",
        "index construction (s)": "index_s",
        "total offline (s)": "total_s",
    }
    for log in logs:
        text = log.read_text(errors="replace")
        for label, field in labels.items():
            match = re.search(rf"^{re.escape(label)}\s+([0-9.]+)\s+([0-9.]+)$", text, re.MULTILINE)
            if not match:
                raise RunnerError(f"cannot parse {label} from {log}")
            records["truss"][field].append(float(match.group(1)))
            records["core"][field].append(float(match.group(2)))
        for label in ("super-nodes", "super-edges", "index size (MB)", "compression, level >= 3"):
            match = re.search(rf"^{re.escape(label)}\s+([0-9.]+)\s+([0-9.]+)$", text, re.MULTILINE)
            if not match:
                raise RunnerError(f"cannot parse {label} from {log}")
            pair = match.groups()
            if label in static and static[label] != pair:
                raise RunnerError(f"inconsistent {label} across runs")
            static[label] = pair

    import statistics
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as handle:
        fields = [
            "dataset", "metric", "repetitions", "decomposition_s", "index_construction_s",
            "total_preparation_s", "estimated_index_mb", "super_nodes", "super_edges",
            "compression_level_ge_3",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for index, metric in enumerate(("truss", "core")):
            writer.writerow({
                "dataset": dataset.key,
                "metric": metric,
                "repetitions": len(logs),
                "decomposition_s": f"{statistics.median(records[metric]['decomposition_s']):.6f}",
                "index_construction_s": f"{statistics.median(records[metric]['index_s']):.6f}",
                "total_preparation_s": f"{statistics.median(records[metric]['total_s']):.6f}",
                "estimated_index_mb": static["index size (MB)"][index],
                "super_nodes": static["super-nodes"][index],
                "super_edges": static["super-edges"][index],
                "compression_level_ge_3": static["compression, level >= 3"][index],
            })


def summarize_fixed(combined_path: Path, dataset: Dataset, output: Path) -> None:
    with combined_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as handle:
        fields = [
            "dataset", "k", "queries", "nonempty_queries", "multi_community_queries",
            "equitruss_mean_ms", "tcp_mean_ms", "indexfree_mean_ms",
            "tcp_over_equi", "free_over_equi", "free_over_tcp", "mean_answer_edges",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        means = {
            field: sum(float(row[field]) for row in rows) / len(rows)
            for field in ("equitruss_ms", "tcp_ms", "indexfree_ms", "answer_edges")
        }
        writer.writerow({
            "dataset": dataset.key,
            "k": dataset.fixed_k,
            "queries": len(rows),
            "nonempty_queries": sum(int(row["answer_edges"]) > 0 for row in rows),
            "multi_community_queries": sum(int(row["communities"]) > 1 for row in rows),
            "equitruss_mean_ms": f"{means['equitruss_ms']:.6f}",
            "tcp_mean_ms": f"{means['tcp_ms']:.6f}",
            "indexfree_mean_ms": f"{means['indexfree_ms']:.6f}",
            "tcp_over_equi": f"{means['tcp_ms'] / means['equitruss_ms']:.3f}",
            "free_over_equi": f"{means['indexfree_ms'] / means['equitruss_ms']:.3f}",
            "free_over_tcp": f"{means['indexfree_ms'] / means['tcp_ms']:.3f}",
            "mean_answer_edges": f"{means['answer_edges']:.3f}",
        })


def sanitize_index_summary(source: Path, dataset_key: str, destination: Path) -> None:
    with source.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0]) if rows else []
    for row in rows:
        row["graph"] = dataset_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sanitize_command_argument(value: str, repo: Path, run_dir: Path, publish_dir: Path) -> str:
    path = Path(value)
    if not path.is_absolute():
        return value
    resolved = path.resolve()
    for root, marker in ((run_dir.resolve(), "$RUN"), (publish_dir.resolve(), "$PUBLISH"), (repo.resolve(), "$REPO")):
        if resolved == root or resolved.is_relative_to(root):
            relative = resolved.relative_to(root)
            return marker if str(relative) == "." else f"{marker}/{relative}"
    return f"$EXTERNAL/{resolved.name}"


def create_command_ledger(manager: RunManager, output: Path) -> None:
    commands = []
    for path in sorted(manager.logs_dir.glob("*/command.json")):
        record = json.loads(path.read_text())
        commands.append({
            "task": path.parent.name,
            "argv": [
                sanitize_command_argument(str(value), manager.repo, manager.run_dir, manager.publish_dir)
                for value in record.get("argv", [])
            ],
        })
    atomic_json(output, {"runner_sha256": manager.runner_hash, "commands": commands})


def copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def create_manifest(root: Path, output: Path, expected_files: set[Path]) -> None:
    root = root.resolve()
    output = output.resolve()
    normalized_expected = {path.resolve() for path in expected_files}
    actual = {
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.resolve() != output.resolve()
    }
    missing = normalized_expected - actual
    extra = actual - normalized_expected
    if missing or extra:
        raise RunnerError(
            "publish inventory mismatch; "
            f"missing={[str(path.relative_to(root)) for path in sorted(missing)]}, "
            f"extra={[str(path.relative_to(root)) for path in sorted(extra)]}"
        )
    entries = [
        {
            "path": str(path.relative_to(root.resolve())),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(normalized_expected)
    ]
    atomic_json(output, {"generated_at_utc": utc_now(), "files": entries})


def dirty_tree_outside_active_publish(repo: Path, run_id: str) -> str:
    result = capture([
        "git", "status", "--porcelain=v1", "--untracked-files=all", "--", ".",
        f":(exclude)remote-results/{run_id}",
        f":(exclude)remote-results/{run_id}/**",
    ], repo)
    if result.get("exit_code") != 0:
        raise RunnerError("cannot inspect Git working tree")
    return str(result.get("stdout", ""))


def verified_task_outputs(run_dir: Path, task_id: str) -> bool:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id)
    state_path = run_dir / "tasks" / f"{safe}.json"
    if not state_path.is_file():
        return False
    try:
        state = json.loads(state_path.read_text())
    except json.JSONDecodeError:
        return False
    if state.get("status") != "complete" or not state.get("outputs"):
        return False
    for raw_path, metadata in state["outputs"].items():
        path = Path(raw_path)
        if not path.is_file() or path.stat().st_size != metadata.get("bytes"):
            return False
        if sha256(path) != metadata.get("sha256"):
            return False
    return True


def validate_run_id(run_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", run_id) or ".." in run_id:
        raise RunnerError("--run-id must be a 1-64 character slug using letters, numbers, '.', '_' or '-'")
    return run_id


def planned_publish_inventory(
    manager: RunManager,
    datasets: Sequence[Dataset],
    args: argparse.Namespace,
    include_dblp_deep: bool,
) -> set[Path]:
    root = manager.publish_dir
    expected = {
        root / "system.json",
        root / "config.json",
        root / "python-dependencies.json",
        root / "commands.json",
        root / "task_summary.csv",
        root / "PUSH_INSTRUCTIONS.txt",
    }
    for dataset in datasets:
        dataset_dir = root / dataset.key
        expected.update({
            dataset_dir / "source.json",
            dataset_dir / "fixed_queries.txt",
            dataset_dir / "highlow_queries.txt",
        })
        if not args.skip_truss_core:
            expected.add(root / "summary" / f"{dataset.key}_truss_core.csv")
        if not args.skip_tcp:
            expected.update({
                root / "summary" / f"{dataset.key}_tcp_equitruss_index.csv",
                dataset_dir / "fixed_tcp.csv",
                dataset_dir / "sweep_tcp.csv",
            })
        if not args.skip_index_free:
            expected.update({dataset_dir / "fixed_no_tcp.csv", dataset_dir / "sweep_no_tcp.csv"})
        if not args.skip_index_free and not args.skip_tcp:
            expected.update({
                dataset_dir / "fixed_combined.csv",
                dataset_dir / "sweep_combined.csv",
                root / "summary" / f"{dataset.key}_fixed.csv",
            })
            if not args.skip_plots:
                expected.update({dataset_dir / "sweep_with_tcp.png", dataset_dir / "sweep_without_tcp.png"})

    if include_dblp_deep:
        deep_dir = root / "dblp-deep"
        expected.add(deep_dir / "highlow_queries.txt")
        if not args.skip_index_free:
            expected.add(deep_dir / "sweep_no_tcp.csv")
        if not args.skip_tcp:
            expected.add(deep_dir / "sweep_tcp.csv")
        if not args.skip_index_free and not args.skip_tcp:
            expected.add(deep_dir / "sweep_combined.csv")
            if not args.skip_plots:
                expected.update({deep_dir / "sweep_with_tcp.png", deep_dir / "sweep_without_tcp.png"})
    return {path.resolve() for path in expected}


def default_run_id() -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-run-{secrets.token_hex(3)}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    parser.add_argument("--execute", action="store_true", help="run the plan; without this flag only print it")
    parser.add_argument("--datasets", default="livejournal,orkut,uk2002", help="comma list or 'all'")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--heap-gb", type=int, default=None, help="JVM Xms/Xmx in GiB; default is 70%% of RAM")
    parser.add_argument("--java-home")
    parser.add_argument("--archive", action="append", default=[], metavar="DATASET=PATH")
    parser.add_argument("--construction-reps", type=int, default=3)
    parser.add_argument("--query-reps", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fixed-per-bucket", type=int, default=100)
    parser.add_argument("--sweep-per-set", type=int, default=100)
    parser.add_argument("--timeout-hours", type=float, default=0, help="per-command timeout; 0 means unlimited")
    parser.add_argument("--skip-index-free", action="store_true")
    parser.add_argument("--skip-tcp", action="store_true")
    parser.add_argument("--skip-truss-core", action="store_true")
    parser.add_argument("--skip-plots", action="store_true")
    parser.add_argument(
        "--install-dependencies", dest="install_dependencies", action="store_true",
        help="create a run-local venv with pinned NetworkX and Matplotlib",
    )
    parser.add_argument("--force-resource-risk", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--reuse-tracked-queries", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def plan_text(datasets: Sequence[Dataset], heap_gib: int, run_id: str, args: argparse.Namespace) -> str:
    lines = [
        f"Run ID: {run_id}",
        f"Datasets: {', '.join(dataset.key for dataset in datasets)}",
        f"JVM heap: {heap_gib} GiB",
        "Stages: download -> normalize -> queries -> compile -> construction -> fixed queries -> sweeps -> merge -> plots -> publish",
        "Timed Java tasks run sequentially and may take hours or days on the largest graphs.",
    ]
    if args.skip_index_free:
        lines.append("Index-Free stages: SKIPPED")
    if args.skip_tcp:
        lines.append("TCP stages: SKIPPED")
    if args.skip_truss_core:
        lines.append("k-truss/core comparison: SKIPPED")
    lines.append("Required RAM guidance: " + ", ".join(f"{dataset.key}≈{dataset.minimum_ram_gib}+ GiB" for dataset in datasets))
    lines.append(f"Recommended free disk: {sum(MINIMUM_DISK_GIB[dataset.key] for dataset in datasets)}+ GiB")
    return "\n".join(lines)


def main() -> None:
    if sys.version_info < (3, 11):
        raise RunnerError("Python 3.11 or newer is required")
    args = parse_args()
    repo = Path(__file__).resolve().parents[1]
    datasets = parse_datasets(args.datasets)
    archives = parse_archive_overrides(args.archive)
    java, javac = resolve_java(args.java_home)
    jar_tool = str(Path(javac).with_name("jar"))
    if capture([jar_tool, "--version"]).get("exit_code") != 0:
        raise RunnerError("JDK jar tool is unavailable")
    memory = physical_memory_bytes()
    if args.heap_gb is None:
        if memory is None:
            raise RunnerError("cannot detect RAM; provide --heap-gb")
        args.heap_gb = max(4, math.floor(memory / 2**30 * 0.70))
    run_id = validate_run_id(args.run_id or default_run_id())

    print(plan_text(datasets, args.heap_gb, run_id, args))
    if not args.execute:
        print("\nPlan only. Add --execute to start.")
        return

    if any(dataset.key == "uk2002" for dataset in datasets) and "uk2002" not in archives:
        raise RunnerError(
            "UK-2002 automatic HTTPS download is not reliable; download the Matrix Market archive from "
            "https://sparse.tamu.edu/LAW/uk-2002 and pass --archive uk2002=/path/to/uk-2002.tar.gz"
        )

    if not args.allow_dirty:
        dirty = dirty_tree_outside_active_publish(repo, run_id)
        if dirty:
            raise RunnerError(
                "source tree has changes outside this run's publish bundle; commit/stash them or pass --allow-dirty"
            )
    if shutil.which("sort") is None:
        raise RunnerError("external numeric sort is required")

    if memory is None and not args.force_resource_risk:
        raise RunnerError("cannot detect installed RAM; use --force-resource-risk to override")
    installed_gib = memory / 2**30 if memory else 0
    risky = [dataset for dataset in datasets if installed_gib and installed_gib < dataset.minimum_ram_gib]
    if risky and not args.force_resource_risk:
        details = ", ".join(f"{dataset.key} recommends {dataset.minimum_ram_gib}+ GiB" for dataset in risky)
        raise RunnerError(
            f"insufficient installed RAM ({installed_gib:.1f} GiB): {details}; "
            "use --force-resource-risk to override"
        )

    current_available = available_memory_bytes()
    native_headroom_gib = max(4, math.ceil(args.heap_gb * 0.10))
    required_available_gib = args.heap_gb + native_headroom_gib
    if current_available is None and not args.force_resource_risk:
        raise RunnerError("cannot detect currently available RAM; use --force-resource-risk to override")
    if current_available is not None and current_available / 2**30 < required_available_gib and not args.force_resource_risk:
        raise RunnerError(
            f"only {current_available / 2**30:.1f} GiB is currently available; the {args.heap_gb} GiB heap "
            f"requires about {required_available_gib} GiB including native/OS headroom"
        )

    disk_free_gib = shutil.disk_usage(repo).free / 2**30
    if args.resume:
        existing_run_dir = (repo / "runs" / run_id).resolve()
        pending_heavy = [
            dataset for dataset in datasets
            if not verified_task_outputs(existing_run_dir, f"{dataset.key}-normalize")
        ]
        required_disk_gib = 5 + sum(MINIMUM_DISK_GIB[dataset.key] for dataset in pending_heavy)
    else:
        required_disk_gib = sum(MINIMUM_DISK_GIB[dataset.key] for dataset in datasets)
    if disk_free_gib < required_disk_gib and not args.force_resource_risk:
        raise RunnerError(
            f"insufficient free disk ({disk_free_gib:.1f} GiB); this run stage recommends "
            f"{required_disk_gib}+ GiB; use --force-resource-risk to override"
        )
    if installed_gib and args.heap_gb + native_headroom_gib > installed_gib:
        raise RunnerError("--heap-gb leaves insufficient memory for the OS and native JVM allocations")

    manager = RunManager(repo, run_id, args.resume, args.timeout_hours)
    manager.initialize()
    try:
        run_config = {
            "run_id": run_id,
            "datasets": [asdict(dataset) for dataset in datasets],
            "heap_gib": args.heap_gb,
            "construction_reps": args.construction_reps,
            "query_reps": args.query_reps,
            "seed": args.seed,
            "fixed_per_bucket": args.fixed_per_bucket,
            "sweep_per_set": args.sweep_per_set,
            "skip_index_free": args.skip_index_free,
            "skip_tcp": args.skip_tcp,
            "skip_truss_core": args.skip_truss_core,
            "skip_plots": args.skip_plots,
            "reuse_tracked_queries": args.reuse_tracked_queries,
            "archive_overrides": {key: path.name for key, path in sorted(archives.items())},
        }
        run_config = json.loads(json.dumps(run_config, sort_keys=True))
        config_path = manager.run_dir / "meta" / "config.json"
        if config_path.exists():
            existing_config = json.loads(config_path.read_text())
            if existing_config.get("configuration") != run_config:
                raise RunnerError("resume configuration differs from the original run")
        else:
            atomic_json(config_path, {"created_at_utc": utc_now(), "configuration": run_config})

        system_path = manager.run_dir / "meta" / "system.json"
        if not system_path.exists():
            atomic_json(system_path, system_information(repo, java, javac, args))

        python = sys.executable
        required_modules = ["networkx"] + ([] if args.skip_plots else ["matplotlib"])
        probe_code = "; ".join(f"import {module}" for module in required_modules)
        probe = subprocess.run([python, "-c", probe_code], capture_output=True)
        if probe.returncode != 0:
            if not args.install_dependencies:
                modules = ", ".join(required_modules)
                raise RunnerError(
                    f"missing Python dependencies ({modules}); pass --install-dependencies"
                    " or install them before running"
                )
            venv = manager.run_dir / "venv"
            manager.run_command(
                "install-dependency-venv",
                [python, "-m", "venv", str(venv)],
                [],
                [venv / "pyvenv.cfg"],
            )
            venv_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            dependency_marker = venv / ".experiment-dependencies-installed"
            packages = ["networkx==3.5"] + ([] if args.skip_plots else ["matplotlib==3.9.4"])

            def install_dependencies_action() -> None:
                completed = subprocess.run(
                    [str(venv_python), "-m", "pip", "install", *packages],
                    cwd=repo,
                    check=False,
                )
                if completed.returncode != 0:
                    raise RunnerError("Python dependency installation failed")
                version_code = "; ".join(
                    [f"import {module}" for module in required_modules]
                    + [f"print({module}.__version__)" for module in required_modules]
                )
                probe_result = subprocess.run(
                    [str(venv_python), "-c", version_code],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if probe_result.returncode != 0:
                    raise RunnerError("Python dependency import failed after installation")
                dependency_marker.write_text(probe_result.stdout.strip() + "\n")

            manager.run_action(
                "install-dependencies",
                {"packages": packages},
                [venv / "pyvenv.cfg"],
                [dependency_marker],
                install_dependencies_action,
            )
            python = str(venv_python)

        dependency_probe = capture([
            python,
            "-c",
            "import importlib.metadata,json; "
            f"print(json.dumps({{name: importlib.metadata.version(name) for name in {required_modules!r}}}, sort_keys=True))",
        ])
        if dependency_probe.get("exit_code") != 0:
            raise RunnerError("cannot record Python dependency versions")
        dependency_versions = json.loads(str(dependency_probe["stdout"]))
        atomic_json(manager.run_dir / "meta" / "python-dependencies.json", {
            "python_executable": Path(python).name,
            "versions": dependency_versions,
        })

        build_dir = manager.run_dir / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        compiled_jar = build_dir / "experiments.jar"
        source_files = sorted((repo / "src").glob("*.java"))

        def compile_action() -> None:
            temporary_classes = build_dir / "classes.partial"
            temporary_jar = build_dir / "experiments.jar.partial"
            shutil.rmtree(temporary_classes, ignore_errors=True)
            temporary_classes.mkdir(parents=True)
            if temporary_jar.exists():
                temporary_jar.unlink()
            completed = subprocess.run(
                [javac, "-encoding", "UTF-8", "-d", str(temporary_classes), *map(str, source_files)],
                cwd=repo,
                check=False,
            )
            if completed.returncode != 0:
                raise RunnerError("Java compilation failed")
            completed = subprocess.run(
                [jar_tool, "--create", "--file", str(temporary_jar), "-C", str(temporary_classes), "."],
                cwd=repo,
                check=False,
            )
            if completed.returncode != 0:
                raise RunnerError("JAR creation failed")
            os.replace(temporary_jar, compiled_jar)
            shutil.rmtree(temporary_classes)

        manager.run_action(
            "compile",
            {"javac": Path(javac).name, "jar": Path(jar_tool).name},
            source_files,
            [compiled_jar],
            compile_action,
        )
        classpath = str(compiled_jar)
        compile_marker = compiled_jar
        heap = [f"-Xms{args.heap_gb}g", f"-Xmx{args.heap_gb}g"]

        toy_output = manager.run_dir / "results" / "toy"
        toy_result = toy_output / "query_results.txt"
        manager.run_command(
            "toy-main", [java, "-Xms1g", "-Xmx1g", "-cp", classpath, "MainCore", "data/toy.txt", str(toy_output), "data/toy_queries.txt"],
            [compile_marker, repo / "data/toy.txt", repo / "data/toy_queries.txt"], [toy_result],
        )
        manager.run_command(
            "toy-oracle", [python, "tools/oracle.py", "data/toy.txt", "data/toy_queries.txt", str(toy_result)],
            [repo / "tools/oracle.py", repo / "data/toy.txt", repo / "data/toy_queries.txt", toy_result], [],
            lambda stdout: None if "all 8 verified queries" in stdout.read_text() else (_ for _ in ()).throw(RunnerError("toy oracle failed")),
        )

        for dataset in datasets:
            archive = archives.get(dataset.key, manager.run_dir / "downloads" / Path(dataset.url).name)
            if dataset.key not in archives:
                manager.run_action(
                    f"{dataset.key}-download", {"url": dataset.url}, [], [archive],
                    lambda dataset=dataset, archive=archive: download(dataset.url, archive),
                )

            graph = manager.run_dir / "data" / f"{dataset.key}.el"
            degree_file = manager.run_dir / "data" / f"{dataset.key}.degrees.bin"
            graph_meta = manager.run_dir / "data" / f"{dataset.key}.json"
            manager.run_action(
                f"{dataset.key}-normalize", {"kind": dataset.archive_kind, "version": 1}, [archive],
                [graph, degree_file, graph_meta],
                lambda dataset=dataset, archive=archive, graph=graph, degree_file=degree_file, graph_meta=graph_meta:
                    normalize_graph(dataset, archive, graph, degree_file, graph_meta, manager.run_dir / "tmp" / dataset.key),
            )

            published_source = manager.publish_dir / dataset.key / "source.json"

            def publish_source_metadata(
                source_meta: Path = graph_meta,
                source_archive: Path = archive,
                destination: Path = published_source,
            ) -> None:
                metadata = json.loads(source_meta.read_text())
                metadata["archive_filename"] = source_archive.name
                metadata["archive_sha256"] = manager.file_hash(source_archive)
                atomic_json(destination, metadata)

            manager.run_action(
                f"{dataset.key}-source-publish",
                {"version": 1},
                [archive, graph_meta],
                [published_source],
                publish_source_metadata,
            )

            query_dir = manager.run_dir / "queries"
            fixed_queries = query_dir / f"{dataset.key}_fixed.txt"
            highlow_queries = query_dir / f"{dataset.key}_highlow.txt"
            tracked = (
                TRACKED_QUERIES.get(dataset.key)
                if args.reuse_tracked_queries
                and args.fixed_per_bucket == 100
                and args.sweep_per_set == 100
                and args.seed == 42
                else None
            )
            if tracked and all((repo / path).is_file() for path in tracked):
                manager.run_action(
                    f"{dataset.key}-queries", {"tracked": tracked}, [repo / tracked[0], repo / tracked[1]],
                    [fixed_queries, highlow_queries],
                    lambda tracked=tracked, fixed_queries=fixed_queries, highlow_queries=highlow_queries: (
                        copy_atomic(repo / tracked[0], fixed_queries), copy_atomic(repo / tracked[1], highlow_queries)
                    ),
                )
            else:
                manager.run_action(
                    f"{dataset.key}-queries",
                    {"fixed_k": dataset.fixed_k, "per_bucket": args.fixed_per_bucket, "per_set": args.sweep_per_set, "seed": args.seed},
                    [degree_file, graph_meta], [fixed_queries, highlow_queries],
                    lambda degree_file=degree_file, graph_meta=graph_meta, fixed_queries=fixed_queries,
                           highlow_queries=highlow_queries, dataset=dataset:
                        generate_queries(degree_file, graph_meta, fixed_queries, highlow_queries, dataset.fixed_k,
                                         args.fixed_per_bucket, args.sweep_per_set, args.seed,
                                         manager.run_dir / "tmp" / f"{dataset.key}-queries"),
                )

            expected_fixed = 10 * args.fixed_per_bucket
            validate_query_files(
                fixed_queries,
                highlow_queries,
                dataset.fixed_k,
                args.fixed_per_bucket,
                args.sweep_per_set,
            )
            published_fixed_queries = manager.publish_dir / dataset.key / "fixed_queries.txt"
            published_highlow_queries = manager.publish_dir / dataset.key / "highlow_queries.txt"
            manager.run_action(
                f"{dataset.key}-query-publish",
                {"version": 1},
                [fixed_queries, highlow_queries],
                [published_fixed_queries, published_highlow_queries],
                lambda fixed=fixed_queries, highlow=highlow_queries,
                       published_fixed=published_fixed_queries, published_highlow=published_highlow_queries: (
                    copy_atomic(fixed, published_fixed), copy_atomic(highlow, published_highlow)
                ),
            )

            dataset_results = manager.run_dir / "results" / dataset.key
            metric_logs: list[Path] = []
            if not args.skip_truss_core:
                for repetition in range(1, args.construction_reps + 1):
                    task = f"{dataset.key}-metric-{repetition}"
                    manager.run_command(
                        task,
                        [java, *heap, "-cp", classpath, "MainMetricCompare", str(graph), str(dataset_results / "metric" / str(repetition))],
                        [compile_marker, graph], [],
                    )
                    metric_logs.append(manager.logs_dir / task / "stdout.log")
                metric_summary = manager.publish_dir / "summary" / f"{dataset.key}_truss_core.csv"
                manager.run_action(
                    f"{dataset.key}-metric-summary", {"parser": 1}, metric_logs, [metric_summary],
                    lambda logs=tuple(metric_logs), dataset=dataset, output=metric_summary: parse_metric_compare(logs, dataset, output),
                )

            isolated_csvs: list[Path] = []
            if not args.skip_tcp:
                for repetition in range(1, args.construction_reps + 1):
                    for method in ("tcp", "equitruss"):
                        output_dir = dataset_results / "isolated-index" / f"{method}-{repetition}"
                        output_csv = output_dir / "index_compare.csv"
                        task = f"{dataset.key}-index-{method}-{repetition}"
                        manager.run_command(
                            task,
                            [java, *heap, "-cp", classpath, "MainIndexCompare", str(graph), str(output_dir), method, str(repetition)],
                            [compile_marker, graph], [output_csv],
                            lambda _stdout, output_csv=output_csv: validate_csv_rows(output_csv, 1),
                        )
                        isolated_csvs.append(output_csv)
                raw_index_summary = dataset_results / "isolated-index" / "summary.csv"
                index_summary = manager.publish_dir / "summary" / f"{dataset.key}_tcp_equitruss_index.csv"
                manager.run_command(
                    f"{dataset.key}-index-summary-raw",
                    [python, "tools/summarize_index_compare.py", str(raw_index_summary), *map(str, isolated_csvs)],
                    [repo / "tools/summarize_index_compare.py", *isolated_csvs], [raw_index_summary],
                    lambda _stdout, output=raw_index_summary: validate_csv_rows(output, 2),
                )
                manager.run_action(
                    f"{dataset.key}-index-summary-publish",
                    {"dataset": dataset.key},
                    [raw_index_summary],
                    [index_summary],
                    lambda source=raw_index_summary, key=dataset.key, destination=index_summary:
                        sanitize_index_summary(source, key, destination),
                )

            fixed_no_tcp_dir = dataset_results / "fixed-no-tcp"
            fixed_tcp_dir = dataset_results / "fixed-tcp"
            combined_dir = dataset_results / "combined"
            fixed_no_tcp = fixed_no_tcp_dir / "bench.csv"
            fixed_tcp = fixed_tcp_dir / "bench.csv"
            combined_fixed = combined_dir / "bench.csv"
            fixed_no_tcp_runs: list[Path] = []
            if not args.skip_index_free:
                for repetition in range(1, args.query_reps + 1):
                    repetition_dir = fixed_no_tcp_dir / f"run-{repetition}"
                    repetition_csv = repetition_dir / "bench.csv"
                    manager.run_command(
                        f"{dataset.key}-fixed-no-tcp-{repetition}",
                        [java, *heap, "-cp", classpath, "MainBenchmark", str(graph), str(repetition_dir), str(fixed_queries), str(expected_fixed)],
                        [compile_marker, graph, fixed_queries], [repetition_csv],
                        lambda stdout, output=repetition_csv, rows=expected_fixed: (
                            require_agreement(stdout), validate_fixed_csv(output, rows)
                        ),
                    )
                    fixed_no_tcp_runs.append(repetition_csv)
                manager.run_action(
                    f"{dataset.key}-fixed-no-tcp-aggregate",
                    {"repetitions": args.query_reps},
                    fixed_no_tcp_runs,
                    [fixed_no_tcp],
                    lambda sources=tuple(fixed_no_tcp_runs), output=fixed_no_tcp:
                        aggregate_fixed_no_tcp(sources, output),
                    lambda output=fixed_no_tcp, rows=expected_fixed: validate_fixed_csv(output, rows),
                )
                published_no_tcp = manager.publish_dir / dataset.key / "fixed_no_tcp.csv"
                manager.run_action(
                    f"{dataset.key}-fixed-no-tcp-publish", {"version": 1}, [fixed_no_tcp], [published_no_tcp],
                    lambda source=fixed_no_tcp, destination=published_no_tcp: copy_atomic(source, destination),
                )
            if not args.skip_tcp:
                manager.run_command(
                    f"{dataset.key}-fixed-tcp",
                    [java, *heap, "-cp", classpath, "MainTcpBenchmark", str(graph), str(fixed_tcp_dir), str(fixed_queries), str(expected_fixed), str(args.query_reps)],
                    [compile_marker, graph, fixed_queries], [fixed_tcp],
                    lambda stdout, output=fixed_tcp, rows=expected_fixed: (require_agreement(stdout), validate_fixed_csv(output, rows)),
                )
                published_tcp = manager.publish_dir / dataset.key / "fixed_tcp.csv"
                manager.run_action(
                    f"{dataset.key}-fixed-tcp-publish", {"version": 1}, [fixed_tcp], [published_tcp],
                    lambda source=fixed_tcp, destination=published_tcp: copy_atomic(source, destination),
                )
            if not args.skip_index_free and not args.skip_tcp:
                manager.run_command(
                    f"{dataset.key}-fixed-merge",
                    [python, "tools/merge_tcp_results.py", "fixed", str(fixed_no_tcp), str(fixed_tcp), str(combined_fixed)],
                    [repo / "tools/merge_tcp_results.py", fixed_no_tcp, fixed_tcp], [combined_fixed],
                    lambda _stdout, output=combined_fixed, rows=expected_fixed: validate_fixed_csv(output, rows),
                )
                publish_fixed = manager.publish_dir / dataset.key / "fixed_combined.csv"
                fixed_summary = manager.publish_dir / "summary" / f"{dataset.key}_fixed.csv"
                manager.run_action(
                    f"{dataset.key}-fixed-publish", {"version": 1}, [combined_fixed], [publish_fixed, fixed_summary],
                    lambda source=combined_fixed, destination=publish_fixed, dataset=dataset, summary=fixed_summary: (
                        copy_atomic(source, destination), summarize_fixed(source, dataset, summary)
                    ),
                )

            sweep_no_tcp_dir = dataset_results / "sweep-no-tcp"
            sweep_tcp_dir = dataset_results / "sweep-tcp"
            sweep_no_tcp = sweep_no_tcp_dir / "sweep.csv"
            sweep_tcp = sweep_tcp_dir / "sweep.csv"
            combined_sweep = combined_dir / "sweep.csv"
            k_spec = ",".join(map(str, dataset.sweep_k))
            expected_cells = len(dataset.sweep_k) * 2
            if not args.skip_index_free:
                manager.run_command(
                    f"{dataset.key}-sweep-no-tcp",
                    [java, *heap, "-cp", classpath, "MainSweep", str(graph), str(sweep_no_tcp_dir), str(highlow_queries), k_spec, str(args.query_reps)],
                    [compile_marker, graph, highlow_queries], [sweep_no_tcp],
                    lambda stdout, output=sweep_no_tcp, cells=expected_cells: (
                        require_agreement(stdout), validate_sweep_csv(output, cells, {"indexed", "free"})
                    ),
                )
                published_sweep_no_tcp = manager.publish_dir / dataset.key / "sweep_no_tcp.csv"
                manager.run_action(
                    f"{dataset.key}-sweep-no-tcp-publish", {"version": 1},
                    [sweep_no_tcp], [published_sweep_no_tcp],
                    lambda source=sweep_no_tcp, destination=published_sweep_no_tcp: copy_atomic(source, destination),
                )
            if not args.skip_tcp:
                manager.run_command(
                    f"{dataset.key}-sweep-tcp",
                    [java, *heap, "-cp", classpath, "MainTcpSweep", str(graph), str(sweep_tcp_dir), str(highlow_queries), k_spec, str(args.query_reps)],
                    [compile_marker, graph, highlow_queries], [sweep_tcp],
                    lambda stdout, output=sweep_tcp, cells=expected_cells: (
                        require_agreement(stdout), validate_sweep_csv(output, cells, {"equitruss", "tcp"})
                    ),
                )
                published_sweep_tcp = manager.publish_dir / dataset.key / "sweep_tcp.csv"
                manager.run_action(
                    f"{dataset.key}-sweep-tcp-publish", {"version": 1},
                    [sweep_tcp], [published_sweep_tcp],
                    lambda source=sweep_tcp, destination=published_sweep_tcp: copy_atomic(source, destination),
                )
            if not args.skip_index_free and not args.skip_tcp:
                manager.run_command(
                    f"{dataset.key}-sweep-merge",
                    [python, "tools/merge_tcp_results.py", "sweep", str(sweep_no_tcp), str(sweep_tcp), str(combined_sweep)],
                    [repo / "tools/merge_tcp_results.py", sweep_no_tcp, sweep_tcp], [combined_sweep],
                    lambda _stdout, output=combined_sweep, cells=expected_cells: validate_sweep_csv(
                        output, cells, {"equitruss", "tcp", "free"}
                    ),
                )
                publish_sweep = manager.publish_dir / dataset.key / "sweep_combined.csv"
                manager.run_action(
                    f"{dataset.key}-sweep-publish", {"version": 1}, [combined_sweep], [publish_sweep],
                    lambda source=combined_sweep, destination=publish_sweep: copy_atomic(source, destination),
                )
                if not args.skip_plots:
                    figure_dir = manager.publish_dir / dataset.key
                    for include_tcp in (False, True):
                        suffix = "with_tcp" if include_tcp else "without_tcp"
                        figure = figure_dir / f"sweep_{suffix}.png"
                        plot_argv = [python, "tools/plot_sweep.py", str(combined_sweep), str(figure), dataset.title]
                        if not include_tcp:
                            plot_argv.append("--without-tcp")
                        plot_argv.extend(["--sample-note", f"{args.sweep_per_set} queries per degree set"])
                        manager.run_command(
                            f"{dataset.key}-plot-{suffix}", plot_argv,
                            [repo / "tools/plot_sweep.py", combined_sweep], [figure],
                        )

            if dataset.key == "dblp" and (repo / "data/dblp_deepcore.txt").is_file():
                # The tracked deep-core workload is retained as a separate targeted experiment.
                deep_queries = repo / "data/dblp_deepcore.txt"
                published_deep_queries = manager.publish_dir / "dblp-deep" / "highlow_queries.txt"
                manager.run_action(
                    "dblp-deep-query-publish",
                    {"version": 1},
                    [deep_queries],
                    [published_deep_queries],
                    lambda: copy_atomic(deep_queries, published_deep_queries),
                )
                deep_ks = "40,50,60,70,80,90,100,113"
                deep_cells = 16
                deep_root = manager.run_dir / "results" / "dblp-deep"
                deep_base = deep_root / "sweep-no-tcp" / "sweep.csv"
                deep_tcp = deep_root / "sweep-tcp" / "sweep.csv"
                deep_combined = deep_root / "combined" / "sweep.csv"
                if not args.skip_index_free:
                    manager.run_command(
                        "dblp-deep-sweep-no-tcp",
                        [java, *heap, "-cp", classpath, "MainSweep", str(graph), str(deep_base.parent), str(deep_queries), deep_ks, str(args.query_reps)],
                        [compile_marker, graph, deep_queries], [deep_base],
                        lambda stdout, output=deep_base: (
                            require_agreement(stdout),
                            validate_sweep_csv(output, deep_cells, {"indexed", "free"}),
                        ),
                    )
                    deep_no_tcp_publish = manager.publish_dir / "dblp-deep" / "sweep_no_tcp.csv"
                    manager.run_action(
                        "dblp-deep-sweep-no-tcp-publish", {"version": 1},
                        [deep_base], [deep_no_tcp_publish],
                        lambda: copy_atomic(deep_base, deep_no_tcp_publish),
                    )
                if not args.skip_tcp:
                    manager.run_command(
                        "dblp-deep-sweep-tcp",
                        [java, *heap, "-cp", classpath, "MainTcpSweep", str(graph), str(deep_tcp.parent), str(deep_queries), deep_ks, str(args.query_reps)],
                        [compile_marker, graph, deep_queries], [deep_tcp],
                        lambda stdout, output=deep_tcp: (
                            require_agreement(stdout),
                            validate_sweep_csv(output, deep_cells, {"equitruss", "tcp"}),
                        ),
                    )
                    deep_tcp_publish = manager.publish_dir / "dblp-deep" / "sweep_tcp.csv"
                    manager.run_action(
                        "dblp-deep-sweep-tcp-publish", {"version": 1},
                        [deep_tcp], [deep_tcp_publish],
                        lambda: copy_atomic(deep_tcp, deep_tcp_publish),
                    )
                if not args.skip_index_free and not args.skip_tcp:
                    manager.run_command(
                        "dblp-deep-merge",
                        [python, "tools/merge_tcp_results.py", "sweep", str(deep_base), str(deep_tcp), str(deep_combined)],
                        [repo / "tools/merge_tcp_results.py", deep_base, deep_tcp], [deep_combined],
                        lambda _stdout: validate_sweep_csv(deep_combined, deep_cells, {"equitruss", "tcp", "free"}),
                    )
                    publish_deep = manager.publish_dir / "dblp-deep" / "sweep_combined.csv"
                    manager.run_action(
                        "dblp-deep-publish", {"version": 1}, [deep_combined], [publish_deep],
                        lambda: copy_atomic(deep_combined, publish_deep),
                    )
                    if not args.skip_plots:
                        for include_tcp in (False, True):
                            suffix = "with_tcp" if include_tcp else "without_tcp"
                            figure = manager.publish_dir / "dblp-deep" / f"sweep_{suffix}.png"
                            plot_argv = [python, "tools/plot_sweep.py", str(deep_combined), str(figure), "DBLP-deep-core"]
                            if not include_tcp:
                                plot_argv.append("--without-tcp")
                            plot_argv.extend(["--set-kind", "core", "--sample-note", "core >= 40; 20 queries per set"])
                            manager.run_command(
                                f"dblp-deep-plot-{suffix}", plot_argv,
                                [repo / "tools/plot_sweep.py", deep_combined], [figure],
                            )

        copy_atomic(manager.run_dir / "meta/system.json", manager.publish_dir / "system.json")
        copy_atomic(manager.run_dir / "meta/config.json", manager.publish_dir / "config.json")
        copy_atomic(
            manager.run_dir / "meta/python-dependencies.json",
            manager.publish_dir / "python-dependencies.json",
        )
        command_ledger = manager.publish_dir / "commands.json"
        create_command_ledger(manager, command_ledger)

        task_rows = []
        for state_path in sorted(manager.tasks_dir.glob("*.json")):
            state = json.loads(state_path.read_text())
            task_rows.append({
                "task": state.get("task"),
                "status": state.get("status"),
                "elapsed_seconds": state.get("elapsed_seconds"),
                "peak_rss_bytes": state.get("peak_rss_bytes"),
                "completed_at": state.get("completed_at"),
            })
        task_summary = manager.publish_dir / "task_summary.csv"
        with task_summary.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["task", "status", "elapsed_seconds", "peak_rss_bytes", "completed_at"], lineterminator="\n")
            writer.writeheader()
            writer.writerows(task_rows)

        instructions = manager.publish_dir / "PUSH_INSTRUCTIONS.txt"
        instructions.write_text(
            f"Experiment run: {run_id}\n\n"
            f"Review the bundle, then run from the repository root:\n"
            f"  git add -- remote-results/{run_id}\n"
            f"  git commit -m \"Add experiment results from {run_id}\"\n"
            f"  git push origin HEAD\n",
            encoding="utf-8",
        )

        expected_publish_files = planned_publish_inventory(
            manager,
            datasets,
            args,
            include_dblp_deep=any(dataset.key == "dblp" for dataset in datasets)
            and (repo / "data/dblp_deepcore.txt").is_file(),
        )
        create_manifest(
            manager.publish_dir,
            manager.publish_dir / "manifest.json",
            expected_publish_files,
        )
        print(f"\nComplete. Git-ready bundle: {manager.publish_dir}")
        print(instructions.read_text())
    finally:
        manager.release_lock()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run with the same --run-id and --resume.", file=sys.stderr)
        raise SystemExit(130)
    except RunnerError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
