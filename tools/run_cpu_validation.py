"""Run the phase 2 CPU validation (ZEXDOC, ZEXALL, z80test) in parallel, one job per group.

Usage (from the project root):
    python -m tools.run_cpu_validation                      # everything
    python -m tools.run_cpu_validation --only zexdoc        # one program or z80test variant
    python -m tools.run_cpu_validation --group daa          # groups whose name contains "daa"

Every ZEX test group and every z80test test runs as a separate job (the harness rewrites the
program's test table, see tests/cpu/exercisers.py), spread over all CPU cores. Progress is
printed as jobs finish; the complete printout of every job goes to output/cpu_validation.log.
Fetch the programs first with tools/fetch_test_data.sh.
"""

from __future__ import annotations

import argparse
import functools
import re
import sys
import time
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path

from tests.cpu import exercisers

LOG_PATH = Path(__file__).resolve().parents[1] / "output" / "cpu_validation.log"


@dataclass(frozen=True)
class Job:
    """One test group of one program.

    Attributes:
        program: "zexdoc"/"zexall" or a z80test variant.
        index: Position of the group in the program's test table.
        name: Name of the group, for reports.
    """

    program: str
    index: int
    name: str


@dataclass(frozen=True)
class JobResult:
    """Outcome of one job.

    Attributes:
        job: The job.
        passed: The group finished and reported no failure.
        seconds: Wall-clock time of the job.
        instructions: Instructions executed.
        output: The program's printout.
    """

    job: Job
    passed: bool
    seconds: float
    instructions: int
    output: str


@functools.cache
def _worker_runner() -> exercisers.TrapRunner:
    """Return this worker process's runner, created on first use (building a CPU is slow)."""
    return exercisers.TrapRunner()


def run_job(job: Job) -> JobResult:
    """Run one test group in a worker process."""
    started = time.perf_counter()
    if job.program in exercisers.ZEX_PROGRAMS:
        result = exercisers.run_zex(_worker_runner(), job.program, groups=[job.index])
        failures = exercisers.zex_failures(result.output)
    else:
        result = exercisers.run_z80test(_worker_runner(), job.program, tests=[job.index])
        failures = exercisers.z80test_failures(result.output)
    passed = result.finished and not failures
    seconds = time.perf_counter() - started
    return JobResult(job, passed, seconds, result.instructions, result.output)


def collect_jobs(only: list[str] | None, group_filter: str | None) -> list[Job]:
    """List the jobs to run.

    Args:
        only: Programs/variants to include, or None for all.
        group_filter: Substring a group name must contain (case-insensitive), or None.

    Returns:
        The jobs, ZEX first (their large groups take longest).
    """
    jobs = []
    for program in exercisers.ZEX_PROGRAMS:
        if only is None or program in only:
            names = exercisers.zex_group_names(program)
            jobs += [Job(program, index, name) for index, name in enumerate(names)]
    for variant in exercisers.Z80TEST_VARIANTS:
        if only is None or variant in only:
            count = exercisers.z80test_count(variant)
            jobs += [Job(variant, index, f"test {index:03d}") for index in range(count)]
    if group_filter:
        jobs = [job for job in jobs if group_filter.lower() in job.name.lower()]
    return jobs


def _z80test_name(output: str) -> str:
    """Extract the test name from the printout of a single z80test test ("000 NAME  OK")."""
    match = re.search(r"^\d{3} (.+?)\s+(OK|FAILED|Skipped)", output, re.MULTILINE)
    return match.group(1) if match else "?"


def main() -> int:
    """Run the selected jobs and print progress and a summary.

    Returns:
        0 if every job passed, 1 otherwise, 2 if the programs are missing.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--only", action="append", help="program or z80test variant (repeatable)")
    parser.add_argument("--group", help="only groups whose name contains this text")
    parser.add_argument(
        "--jobs", type=int, default=None, help="worker processes (default: all cores)"
    )
    args = parser.parse_args()

    try:
        jobs = collect_jobs(args.only, args.group)
    except FileNotFoundError:
        print("test programs missing; run tools/fetch_test_data.sh", file=sys.stderr)
        return 2
    LOG_PATH.parent.mkdir(exist_ok=True)
    started = time.perf_counter()
    results: list[JobResult] = []
    with Pool(args.jobs) as pool, LOG_PATH.open("w") as log:
        for result in pool.imap_unordered(run_job, jobs, chunksize=1):
            results.append(result)
            job = result.job
            name = (
                job.name if job.program in exercisers.ZEX_PROGRAMS else _z80test_name(result.output)
            )
            status = "OK" if result.passed else "FAILED"
            elapsed = time.perf_counter() - started
            print(
                f"[{len(results)}/{len(jobs)} {elapsed / 60:5.1f} min] {job.program:<12} "
                f"{name:<34} {status:<6} {result.instructions / 1e6:9.1f} M instr "
                f"{result.seconds:7.1f} s",
                flush=True,
            )
            log.write(f"===== {job.program} #{job.index} {name}: {status}\n{result.output}\n")
            log.flush()

    failed = [result for result in results if not result.passed]
    total_minutes = (time.perf_counter() - started) / 60
    print(
        f"\n{len(results)} groups, {len(failed)} failed, {total_minutes:.1f} min; log: {LOG_PATH}"
    )
    for result in failed:
        print(f"FAILED {result.job.program} #{result.job.index} {result.job.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
