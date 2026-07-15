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

"""
Script to parse and prepare test262 tests into a single directory
so they can be imported and compiled by Fuzzilli.
"""

import argparse
import glob
import os
from pathlib import Path

from parsers import Test262MetaDataParser

# A neutered version of assert APIs to avoid test failures crashing fuzzer generated snippets
ASSERT_NEUTER = """
function assert(mustBeTrue, message) {}
assert.sameValue = function(expected, actual, message) {};
assert.notSameValue = function(expected, actual, message) {};
assert.throws = function(errorConstructor, run) {
  try { run(); } catch(e) {}
};
"""

def process_file(path, dest_dir, base_path, parser):
    abspath = Path(path).resolve()

    try:
        relpath = abspath.relative_to(base_path)
    except ValueError:
        print(f"[{path}] is not within V8 directory {base_path}")
        return 0

    # Parse and check metadata
    if not parser.is_supported(abspath, relpath):
        return 0

    with open(abspath, 'r', encoding='utf-8') as f:
        content = f.read()

    prefix = "load('test/test262/data/harness/sta.js');\n"
    prefix += ASSERT_NEUTER

    # load test262 metadata record to parse includes
    record = parser.parse(content, relpath)
    for inc in record.get('includes', []):
        if inc != 'assert.js':
            prefix += f"load('test/test262/data/harness/{inc}');\n"

    dst_path = os.path.join(dest_dir, os.path.basename(path))
    base_name, ext = os.path.splitext(os.path.basename(path))
    index = 0
    while os.path.exists(dst_path):
        dst_path = os.path.join(dest_dir, f"{base_name}_{index}{ext}")
        index += 1

    with open(dst_path, 'w', encoding='utf-8') as f:
        f.write(prefix + content)
    return 1


def setup_corpus(v8_dir, corpus_dir, patterns):
    os.makedirs(corpus_dir, exist_ok=True)

    base_path = Path(v8_dir).resolve()
    parser = Test262MetaDataParser(base_path)

    files_to_process = []
    for pattern in patterns:
        for f in glob.glob(pattern, recursive=True):
            files_to_process.append(f)

    print(f"Discovered {len(files_to_process)} file candidates.")

    results = [process_file(f, corpus_dir, base_path, parser) for f in files_to_process]

    print(f"Successfully prepared {sum(results)} test262 files in {corpus_dir}")

def main():
    parser = argparse.ArgumentParser(description="Prepare test262 corpus for Fuzzilli")
    parser.add_argument('--v8-dir', required=True, help="Absolute path to the V8 checkout directory")
    parser.add_argument('--corpus-dir', required=True, help="Destination directory for prepared tests")
    parser.add_argument('--pattern', action='append', required=True, help="Glob pattern for test files (can be multiple)")
    args = parser.parse_args()

    setup_corpus(
        os.path.abspath(args.v8_dir),
        os.path.abspath(args.corpus_dir),
        args.pattern
    )

if __name__ == '__main__':
    main()
