"""
FoG tools package. Re-exports all IkaTools and plain functions get_v8_path, edit_template_by_diff.
"""

from .path_fs import (
    get_v8_path,
    run_python_tool,
    get_v8_path_tool,
    get_realpath_tool,
    tree_tool,
    ripgrep_tool,
    fuzzy_finder_tool,
    read_file_tool,
)
from .agent_memory import (
    write_agent_memory_tool,
    read_agent_memory_tool,
    list_agent_memory_ids_tool,
)
from .fuzzil import (
    lift_fuzzil_to_js_tool,
    compile_js_to_fuzzil_tool,
)
from .js_entry import (
    search_js_file_name_by_pattern_tool,
    get_js_entry_data_by_name_tool,
    get_all_js_file_names_tool,
    get_random_entry_data_tool,
)
from .template_search import (
    get_all_template_names_from_json_tool,
    get_template_from_json_by_name_tool,
    search_template_file_json_tool,
    search_regex_template_swift_tool,
    search_regex_template_fuzzil_tool,
    get_random_template_swift_tool,
    get_random_template_fuzzil_tool,
    similar_template_swift_tool,
    similar_template_fuzzil_tool,
)
from .swift_fs import (
    swift_fuzzy_finder_tool,
    swift_tree_tool,
    swift_ripgrep_tool,
    swift_read_file_tool,
)
from .program_template import (
    edit_template_by_diff,
    write_program_template_tool,
    list_program_templates_tool,
    remove_program_template_tool,
    remove_program_template_weight_tool,
    edit_template_by_diff_tool,
    compile_program_template_tool,
    execute_javascript_program_tool,
    list_d8_flags_tool,
    remove_old_javascript_programs_tool,
)

__all__ = [
    "get_v8_path",
    "edit_template_by_diff",
    "run_python_tool",
    "get_v8_path_tool",
    "get_realpath_tool",
    "tree_tool",
    "ripgrep_tool",
    "fuzzy_finder_tool",
    "read_file_tool",
    "write_agent_memory_tool",
    "read_agent_memory_tool",
    "list_agent_memory_ids_tool",
    "lift_fuzzil_to_js_tool",
    "compile_js_to_fuzzil_tool",
    "search_js_file_name_by_pattern_tool",
    "get_js_entry_data_by_name_tool",
    "get_all_js_file_names_tool",
    "get_random_entry_data_tool",
    "get_all_template_names_from_json_tool",
    "get_template_from_json_by_name_tool",
    "search_template_file_json_tool",
    "search_regex_template_swift_tool",
    "search_regex_template_fuzzil_tool",
    "get_random_template_swift_tool",
    "get_random_template_fuzzil_tool",
    "similar_template_swift_tool",
    "similar_template_fuzzil_tool",
    "swift_fuzzy_finder_tool",
    "swift_tree_tool",
    "swift_ripgrep_tool",
    "swift_read_file_tool",
    "write_program_template_tool",
    "list_program_templates_tool",
    "remove_program_template_tool",
    "remove_program_template_weight_tool",
    "edit_template_by_diff_tool",
    "compile_program_template_tool",
    "execute_javascript_program_tool",
    "list_d8_flags_tool",
    "remove_old_javascript_programs_tool",
]
