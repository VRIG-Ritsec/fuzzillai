import importlib.machinery
import importlib.util
import os


class DefaultMetaDataParser:
  """Class instantiated once per test configuration/suite, providing a
  method to check for supported tests based on their metadata.
  """
  def __init__(self, _):
    pass

  def is_supported(self, abspath, relpath):
    return any(relpath.name.endswith(ext) for ext in ['.js', '.mjs'])

  def get_harness_files(self, abspath, harness_dir):
    return []


class Test262MetaDataParser(DefaultMetaDataParser):
  def __init__(self, base_dir):
    """Metadata parsing for Test262 analog to the V8 test suite definition."""
    tools_abs_path = os.path.join(base_dir, 'test/test262/data/tools/packaging/parseTestRecord.py')
    loader = importlib.machinery.SourceFileLoader('parseTestRecord', tools_abs_path)
    spec = importlib.util.spec_from_loader('parseTestRecord', loader)
    parseTestRecord_module = importlib.util.module_from_spec(spec)
    loader.exec_module(parseTestRecord_module)
    self.parse = parseTestRecord_module.parseTestRecord
    self.excluded_suffixes = ['_FIXTURE.js']

  def is_supported(self, abspath, relpath):
    if not super().is_supported(abspath, relpath):
      return False

    if any(relpath.name.endswith(suffix) for suffix in self.excluded_suffixes):
      return False

    with open(abspath, encoding='utf-8') as f:
      content = f.read()

    # Needs a relpath style identifier for error reporting in parseTestRecord
    # but not truly important what it is.
    record = self.parse(content, relpath)
    # We don't support negative tests, which typically exhibit syntax errors.
    return 'negative' not in record

  def get_harness_files(self, abspath, harness_dir):
    with open(abspath, encoding='utf-8') as f:
      content = f.read()
    record = self.parse(content, abspath)
    harness_files = [
        os.path.join(harness_dir, "sta.js"),
        os.path.join(harness_dir, "assert.js")
    ]
    for inc in record.get('includes', []):
      harness_files.append(os.path.join(harness_dir, inc))
    return harness_files
