"""
FoG tools package. Re-exports all IkaTools and plain functions.
"""

from .path_fs import (
    get_v8_path,
    run_python_tool,
    get_v8_path_tool,
    get_realpath_tool,
    list_dir_tool,
    glob_search_tool,
    grep_search_tool,
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
    swift_list_dir_tool,
    swift_glob_search_tool,
    swift_grep_search_tool,
    swift_read_file_tool,
)
from .file_patch import (
    swift_patch_file_tool,
    swift_multi_patch_file_tool,
    v8_patch_file_tool,
    v8_multi_patch_file_tool,
    upsert_program_template_tool,
    remove_program_template_tool,
)
from .run_hygiene import (
    fog_template_preflight_tool,
    fog_template_restore_baseline_tool,
    fog_template_postrun_report_tool,
    prepare_clean_fog_run,
    restore_template_baseline,
    write_postrun_hygiene_report,
)
from .program_template import (
    edit_program_template_file_tool,
    list_program_templates_tool,
    compile_program_template_tool,
    execute_javascript_program_tool,
    list_d8_flags_tool,
)

__all__ = [
    "get_v8_path",
    "run_python_tool",
    "get_v8_path_tool",
    "get_realpath_tool",
    "list_dir_tool",
    "glob_search_tool",
    "grep_search_tool",
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
    "swift_list_dir_tool",
    "swift_glob_search_tool",
    "swift_grep_search_tool",
    "swift_read_file_tool",
    "swift_patch_file_tool",
    "swift_multi_patch_file_tool",
    "v8_patch_file_tool",
    "v8_multi_patch_file_tool",
    "upsert_program_template_tool",
    "remove_program_template_tool",
    "fog_template_preflight_tool",
    "fog_template_restore_baseline_tool",
    "fog_template_postrun_report_tool",
    "prepare_clean_fog_run",
    "restore_template_baseline",
    "write_postrun_hygiene_report",
    "edit_program_template_file_tool",
    "list_program_templates_tool",
    "compile_program_template_tool",
    "execute_javascript_program_tool",
    "list_d8_flags_tool",
]
