# Copyright 2025 Google LLC
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

import contextlib
import glob
import io
import os
import unittest

from pathlib import Path
from pyfakefs import fake_filesystem_unittest

import setup_test262

TEST_DATA = Path(__file__).parent / 'testdata'

class TestSetupTest262(fake_filesystem_unittest.TestCase):

  @fake_filesystem_unittest.patchfs(allow_root_user=True)
  def test_setup_corpus(self, fs):
    base_dir = TEST_DATA / 'transpile_full_run' / 'v8'
    fs.create_dir('/output')
    fs.add_real_directory(base_dir)

    patterns = [
      str(base_dir / 'test' / 'test262' / 'data' / 'test' / '**' / '*.js')
    ]

    f = io.StringIO()
    with contextlib.redirect_stdout(f):
      setup_test262.setup_corpus(
          str(base_dir),
          '/output',
          patterns
      )

    # Verify the output format from std-out
    output_lines = f.getvalue().strip().split('\n')
    self.assertTrue(output_lines[0].startswith("Discovered 6 file candidates."))
    self.assertTrue(output_lines[1].startswith("Successfully prepared 4 test262 files in /output"))

    # Verify the written output files (flattened names).
    # Expected: Test1, Test2, Test3, Test4_fail, because
    # Test3_FIXTURE and Test4_negative should be excluded by the parser.
    expected_files = [
      '/output/Test1.js',
      '/output/Test2.js',
      '/output/Test3.js',
      '/output/Test4_fail.js'
    ]

    # Check that our created files match
    output_files = glob.glob('/output/*.js')
    self.assertCountEqual(expected_files, output_files)

    # Read one of the output files to ensure neutered asserts & harness files are prepended
    with open('/output/Test1.js', 'r') as out_f:
      content = out_f.read()

    # Should include sta.js and neutered assert polyfills
    self.assertIn("load('test/test262/data/harness/sta.js');", content)
    self.assertIn("function assert(mustBeTrue, message) {}", content)


if __name__ == '__main__':
  unittest.main()
