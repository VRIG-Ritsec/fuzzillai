"""
Generate folder tools for variant analysis.
"""

import os
import json

from IkaCore.tools import IkaTools

from ._shared import _get_varianal_folder


def create_generate_folder() -> str:
    folder = _get_varianal_folder()
    os.makedirs(folder, exist_ok=True)
    return json.dumps({"message": f"Folder {folder} created"})


def write_to_generate_folder(file_name: str, content: str) -> str:
    folder = _get_varianal_folder()
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, file_name), "w") as f:
        f.write(content)
    return json.dumps({"message": f"File {file_name} written to folder {folder} with content:\n\n {content[:500]}..."})


def read_from_generate_folder(file_name: str) -> str:
    folder = _get_varianal_folder()
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, file_name)

    if not os.path.exists(file_path):
        available_files = os.listdir(folder)
        return json.dumps({
            "error": f"File '{file_name}' not found in folder {folder}",
            "available_files": available_files,
            "workflow_hint": "To read a crash program: (1) get_program_js_from_hash(hash) to fetch from DB, (2) write_to_generate_folder(filename, js_code) to save it, (3) then read_from_generate_folder(filename) to read it",
            "note": "This folder only contains files you've written. Programs must be fetched from database first."
        }, indent=2)

    try:
        with open(file_path, "r") as f:
            content = f.read()
        return content
    except Exception as e:
        return json.dumps({"error": f"Failed to read file: {str(e)}"}, indent=2)


def list_generate_folder() -> str:
    folder = _get_varianal_folder()
    os.makedirs(folder, exist_ok=True)
    files = os.listdir(folder)
    return json.dumps({
        "folder": folder,
        "files": files,
        "count": len(files)
    }, indent=2)


def delete_files_from_generate_folder(file_name: str) -> str:
    folder = _get_varianal_folder()
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, file_name)

    if not os.path.exists(file_path):
        return json.dumps({"error": f"File '{file_name}' not found in folder {folder}"}, indent=2)

    try:
        os.remove(file_path)
        return json.dumps({"message": f"File {file_name} deleted from folder {folder}"})
    except Exception as e:
        return json.dumps({"error": f"Failed to delete file: {str(e)}"}, indent=2)


create_generate_folder_tool = IkaTools(
    name="create_generate_folder",
    description="Create the variant analysis workspace folder. Call before storing crash programs, variants, or analysis outputs.",
    parameters={"N/A": "N/A"},
    execute_function=lambda x: create_generate_folder(),
)

write_to_generate_folder_tool = IkaTools(
    name="write_to_generate_folder",
    description="Write a file to the variant analysis folder. Store crash programs (from get_program_js_from_hash), program variants, or analysis results. Use exact file names like crash_original.js or variant_1.js.",
    parameters={
        "file_name": {"type": "string", "description": "Exact file name (e.g., crash_original.js, variant_1.js)", "required": True},
        "content": {"type": "string", "description": "Full file content: JS code or text analysis", "required": True},
    },
    execute_function=lambda x: write_to_generate_folder(x["file_name"], x["content"]),
)

read_from_generate_folder_tool = IkaTools(
    name="read_from_generate_folder",
    description="Read a file from the variant analysis folder. Call list_generate_folder first to get valid file names. For crash programs, use get_program_js_from_hash instead.",
    parameters={
        "file_name": {"type": "string", "description": "Exact file name from list_generate_folder output", "required": True},
    },
    execute_function=lambda x: read_from_generate_folder(x["file_name"]),
)

list_generate_folder_tool = IkaTools(
    name="list_generate_folder",
    description="List all files in the variant analysis folder. Call before read_from_generate_folder to ensure the file exists.",
    parameters={"N/A": "N/A"},
    execute_function=lambda x: list_generate_folder(),
)

delete_files_from_generate_folder_tool = IkaTools(
    name="delete_files_from_generate_folder",
    description="Delete a file from the variant analysis folder. Use to remove failed variants or temporary analysis files.",
    parameters={
        "file_name": {"type": "string", "description": "Exact file name from list_generate_folder output", "required": True},
    },
    execute_function=lambda x: delete_files_from_generate_folder(x["file_name"]),
)
