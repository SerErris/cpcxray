"""Run the SingleStepTests/z80 suite in parallel and summarise failures per opcode file.

Usage: python tools/run_singlestep.py [--limit N] [--show N] [pattern ...]
Fetch the data first with tools/fetch_test_data.sh.
"""

from __future__ import annotations

import argparse
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "cpu"))

import singlestep

_runner: singlestep.Runner | None = None


def _run_file(job: tuple[Path, int | None, int]) -> tuple[str, int, int, list[str]]:
    """Run the tests of one opcode file in a worker process.

    Returns:
        (file stem, tests run, tests failed, up to `show` failure descriptions).
    """
    global _runner
    if _runner is None:
        _runner = singlestep.Runner()
    path, limit, show = job
    tests = singlestep.load(path)[:limit]
    failed = 0
    samples: list[str] = []
    for t in tests:
        errors = _runner.run(t)
        if errors:
            failed += 1
            if len(samples) < show:
                samples.append(f"  {t['name']}: " + "; ".join(errors))
    return path.stem, len(tests), failed, samples


def main() -> int:
    """Run the selected files in parallel and print progress and failures.

    Returns:
        0 if all tests passed, 1 on failures, 2 if the test data is missing.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patterns", nargs="*", help="file name globs, e.g. 'ed b*'")
    parser.add_argument("--limit", type=int, default=None, help="tests per file")
    parser.add_argument("--show", type=int, default=2, help="failures shown per file")
    args = parser.parse_args()

    files = singlestep.data_files()
    if not files:
        print("no test data; run tools/fetch_test_data.sh", file=sys.stderr)
        return 2
    if args.patterns:
        files = [f for f in files if any(f.match(p + ".json") for p in args.patterns)]

    total = failed_total = bad_files = 0
    with Pool() as pool:
        for done, (name, n, failed, samples) in enumerate(
            pool.imap_unordered(_run_file, [(f, args.limit, args.show) for f in files]), 1
        ):
            if done % 50 == 0 or done == len(files):
                print(
                    f"[{done}/{len(files)} files, {total + n} tests, "
                    f"{failed_total + failed} failed]",
                    flush=True,
                )
            total += n
            failed_total += failed
            if failed:
                bad_files += 1
                print(f"{name}: {failed}/{n} failed", flush=True)
                for s in samples:
                    print(s, flush=True)
    print(f"\n{len(files)} files, {total} tests, {failed_total} failed in {bad_files} files")
    return 1 if failed_total else 0


if __name__ == "__main__":
    sys.exit(main())
