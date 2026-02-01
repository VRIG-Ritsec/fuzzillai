"""
IkaTools registry: IkaTools with explicit executors for every tool function.
Uses direct IkaTools class instantiation instead of function adapters.
"""

import sys
from pathlib import Path
from typing import List

_ikacore_src = Path(__file__).resolve().parent.parent / "IkaCore" / "src"
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

# Import all tools from the new IkaTools-based modules
from tools.FoG_tools_ika import (
    run_python_tool,
    get_v8_path_tool,
    get_realpath_tool,
    tree_tool,
    ripgrep_tool,
    fuzzy_finder_tool,
    read_file_tool,
    lift_fuzzil_to_js_tool,
    compile_js_to_fuzzil_tool,
    write_rag_db_id_tool,
    read_rag_db_id_tool,
    get_runtime_db_ids_tool,
    search_js_file_name_by_pattern_tool,
    get_js_entry_data_by_name_tool,
    get_all_js_file_names_tool,
    get_random_entry_data_tool,
    get_all_template_names_from_json_tool,
    get_template_from_json_by_name_tool,
    search_template_file_json_tool,
    search_regex_template_swift_tool,
    search_regex_template_fuzzil_tool,
    get_random_template_swift_tool,
    get_random_template_fuzzil_tool,
    similar_template_swift_tool,
    similar_template_fuzzil_tool,
    swift_fuzzy_finder_tool,
    swift_tree_tool,
    swift_ripgrep_tool,
    swift_read_file_tool,
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

from tools.rag_tools_ika import (
    search_knowledge_base_tool,
    search_knowledge_base_hybrid_tool,
    get_knowledge_doc_tool,
    search_v8_source_rag_tool,
    search_v8_source_rag_hybrid_tool,
    get_v8_source_rag_doc_tool,
    search_chromium_issues_rag_tool,
    search_chromium_issues_rag_hybrid_tool,
)

from tools.common_tools_ika import (
    web_search_tool,
    get_cfg_for_tool,
    get_call_graph_hashmap_tool,
    find_functions_by_simple_name_tool,
    find_functions_by_fully_qualified_name_tool,
    get_call_graph_node_tool,
)


def fog_george_foreman_tools() -> List[IkaTools]:
    """Tools for GeorgeForeman agent - validates program templates."""
    return [
        get_all_template_names_from_json_tool,
        get_template_from_json_by_name_tool,
        search_template_file_json_tool,
        search_regex_template_swift_tool,
        search_regex_template_fuzzil_tool,
        similar_template_swift_tool,
        similar_template_fuzzil_tool,
        web_search_tool,
        read_rag_db_id_tool,
        get_runtime_db_ids_tool,
        search_knowledge_base_tool,
        search_knowledge_base_hybrid_tool,
        get_knowledge_doc_tool,
        search_v8_source_rag_tool,
        search_v8_source_rag_hybrid_tool,
        get_v8_source_rag_doc_tool,
        search_chromium_issues_rag_tool,
        search_chromium_issues_rag_hybrid_tool,
    ]


def fog_compiler_tools() -> List[IkaTools]:
    """Tools for Compiler agent - compiles program templates."""
    return [
        swift_fuzzy_finder_tool,
        swift_tree_tool,
        swift_ripgrep_tool,
        swift_read_file_tool,
        write_program_template_tool,
        edit_template_by_diff_tool,
        remove_program_template_tool,
        remove_program_template_weight_tool,
        compile_program_template_tool,
        execute_javascript_program_tool,
        list_program_templates_tool,
        web_search_tool,
        remove_old_javascript_programs_tool,
        list_d8_flags_tool,
    ]


def fog_reviewer_of_code_tools() -> List[IkaTools]:
    """Tools for ReviewerOfCode agent - reviews code using RAG database."""
    return [
        fuzzy_finder_tool,
        ripgrep_tool,
        tree_tool,
        web_search_tool,
        search_knowledge_base_tool,
        search_knowledge_base_hybrid_tool,
        get_knowledge_doc_tool,
        search_v8_source_rag_hybrid_tool,
        get_v8_source_rag_doc_tool,
        search_chromium_issues_rag_tool,
        search_chromium_issues_rag_hybrid_tool,
    ]


def fog_v8_search_tools() -> List[IkaTools]:
    """Tools for V8Search agent - searches V8 source code."""
    return [
        fuzzy_finder_tool,
        ripgrep_tool,
        tree_tool,
        read_rag_db_id_tool,
        write_rag_db_id_tool,
        read_file_tool,
        get_realpath_tool,
        get_runtime_db_ids_tool,
        get_cfg_for_tool,
        get_call_graph_hashmap_tool,
        find_functions_by_simple_name_tool,
        find_functions_by_fully_qualified_name_tool,
        get_call_graph_node_tool,
    ]


def fog_code_analyzer_tools() -> List[IkaTools]:
    """Tools for CodeAnalyzer agent - analyzes code and coordinates retrieval."""
    return [
        run_python_tool,
        lift_fuzzil_to_js_tool,
        compile_js_to_fuzzil_tool,
        web_search_tool,
        search_knowledge_base_tool,
        search_knowledge_base_hybrid_tool,
        get_knowledge_doc_tool,
        read_rag_db_id_tool,
        search_v8_source_rag_tool,
        search_v8_source_rag_hybrid_tool,
        get_v8_source_rag_doc_tool,
        get_runtime_db_ids_tool,
        search_chromium_issues_rag_tool,
        search_chromium_issues_rag_hybrid_tool,
    ]


def fog_program_builder_tools() -> List[IkaTools]:
    """Tools for ProgramBuilder agent - builds program templates."""
    return [
        get_all_template_names_from_json_tool,
        get_template_from_json_by_name_tool,
        list_program_templates_tool,
        get_random_template_swift_tool,
        get_random_template_fuzzil_tool,
        search_template_file_json_tool,
        search_regex_template_swift_tool,
        search_regex_template_fuzzil_tool,
        similar_template_swift_tool,
        similar_template_fuzzil_tool,
    ]


def fog_pick_section_tools() -> List[IkaTools]:
    """Tools for PickSection agent - picks V8 code sections to analyze."""
    return [
        search_js_file_name_by_pattern_tool,
        get_js_entry_data_by_name_tool,
        get_all_js_file_names_tool,
        get_random_entry_data_tool,
        search_knowledge_base_tool,
        search_knowledge_base_hybrid_tool,
        get_knowledge_doc_tool,
        search_v8_source_rag_hybrid_tool,
        list_program_templates_tool,
        list_d8_flags_tool,
    ]


def fog_father_of_george_tools() -> List[IkaTools]:
    """Tools for FatherOfGeorge agent - root manager and orchestrator."""
    return [
        search_knowledge_base_tool,
        search_knowledge_base_hybrid_tool,
        get_knowledge_doc_tool,
        search_v8_source_rag_tool,
        search_v8_source_rag_hybrid_tool,
        get_v8_source_rag_doc_tool,
        web_search_tool,
        search_chromium_issues_rag_tool,
        search_chromium_issues_rag_hybrid_tool,
    ]
