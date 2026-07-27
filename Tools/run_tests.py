#!/usr/bin/env python3

# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import sys
import os
import subprocess
import tempfile

def run_tests_and_parse():
    if len(sys.argv) < 2:
        print("Usage: ./run_tests.py <expected_failures_file> [swift_test_args...]")
        return 1

    expected_file_path = sys.argv[1]
    test_args = sys.argv[2:]

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = os.path.join(temp_dir, "event_stream.json")
        cmd = ["swift", "test", "--event-stream-output-path", temp_path] + test_args
        print(f"Running command: {' '.join(cmd)}\n", flush=True)

        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            print("\nTest run interrupted by user.")
            return 130

        tests_metadata = {}
        actual_failures = set()
        run_tests = set()

        for line in open(temp_path, "r").read().strip().split("\n"):
            data = json.loads(line)
            kind = data.get("kind")
            payload = data.get("payload", {})

            if kind == "test" and payload.get("kind") != "suite":
                test_id = payload.get("id")
                if test_id:
                    tests_metadata[test_id] = payload.get("name", test_id)
            elif kind == "event":
                event_kind = payload.get("kind")
                test_id = payload.get("testID")

                if event_kind in ("testStarted", "testEnded") and test_id:
                    run_tests.add(test_id)
                if event_kind == "issueRecorded":
                    issue = payload.get("issue", {})
                    if issue.get("isFailure", False) and test_id:
                        actual_failures.add(test_id)

    # Read expected failures.
    try:
        expected_failures = {
            line.strip()
            for line in open(expected_file_path, "r")
            if line.strip() and not line.strip().startswith("#")
        }
    except FileNotFoundError:
        print(f"Error: Expected failures file '{expected_file_path}' not found.")
        return 1

    # Map test IDs to their simple names.
    run_names = {tests_metadata[tid] for tid in run_tests if tid in tests_metadata}
    actual_failing_names = {tests_metadata[tid] for tid in actual_failures if tid in tests_metadata}

    # Calculate and print results.
    unexpected_failures = actual_failing_names - expected_failures
    # Only include tests that were actually run, so that `--filter=SomeSubSet` doesn't fail because
    # of tests being skipped that are expected to fail.
    expected_failures = expected_failures & run_names
    unexpected_passes = expected_failures - actual_failing_names

    if not unexpected_failures and not unexpected_passes:
        print(f"\nAll run tests matched expectations ({len(expected_failures)} expected failure(s))")
        return 0

    for (title, unexpected) in ("Failures", unexpected_failures), ("Passes", unexpected_passes):
        if unexpected:
            print(f"\nUnexpected {title}:\n{'\n'.join(map(lambda name: f'  • {name}', sorted(unexpected)))}")

    return 1

if __name__ == "__main__":
    sys.exit(run_tests_and_parse())
