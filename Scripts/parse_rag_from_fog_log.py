#!/usr/bin/env python3
"""
Parse RAG questions and results from FatherOfGod (FoG) agent log files (.ans).
Extracts queries to search_v8_source_rag_hybrid, search_knowledge_base_hybrid,
and get_knowledge_doc, along with their resulting files/data.
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path


RAG_TOOLS = frozenset({
    "search_v8_source_rag_hybrid",
    "search_knowledge_base_hybrid",
    "get_knowledge_doc",
})


def strip_box_line(line: str) -> str:
    match = re.search(r"[│|]\s*(.*?)\s*[│|]\s*(?:\033\[0m|\[0m)?\s*$", line)
    if match:
        return match.group(1).strip()
    match = re.search(r"[│|]\s*(.*?)\s*[│|]", line)
    if match:
        return match.group(1).strip()
    return line


def extract_tool_calls_from_llm(line: str) -> list[dict]:
    if "tool_calls_from_llm:" not in line:
        return []
    idx = line.index("tool_calls_from_llm:") + len("tool_calls_from_llm:")
    rest = line[idx:].strip()
    if not rest:
        return []
    try:
        calls = ast.literal_eval(rest)
    except (ValueError, SyntaxError):
        return []
    if not isinstance(calls, list):
        calls = [calls] if calls else []
    out = []
    for c in calls:
        if not isinstance(c, dict):
            continue
        fn = c.get("function") or c.get("function_call", {})
        if isinstance(fn, dict):
            name = fn.get("name")
            args_str = fn.get("arguments", "{}")
        else:
            continue
        if name and name in RAG_TOOLS:
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            out.append({"tool": name, "arguments": args})
    return out


def _extract_files_from_raw(raw: str) -> list[dict]:
    files = []
    for m in re.finditer(r'"file"\s*:\s*"([^"]+)"', raw):
        files.append({"file": m.group(1)})
    for m in re.finditer(r'"doc_id"\s*:\s*"([^"]+)"', raw):
        if files and "doc_id" not in files[-1]:
            files[-1]["doc_id"] = m.group(1)
        elif not files:
            files.append({"doc_id": m.group(1)})
    for m in re.finditer(r'"similarity"\s*:\s*([\d.]+)', raw):
        if files and "similarity" not in files[-1]:
            files[-1]["similarity"] = float(m.group(1))
    return files[:30]


def summarize_rag_result(raw: str, tool: str, max_content_len: int = 2000) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fallback = _extract_files_from_raw(raw)
        if fallback:
            return {"parse_error": True, "extracted_files": fallback}
        return {"raw_preview": raw[:max_content_len], "parse_error": True}

    if isinstance(data, list):
        items = []
        for i, doc in enumerate(data[:20]):
            if not isinstance(doc, dict):
                continue
            item = {
                "doc_id": doc.get("doc_id"),
                "file": doc.get("file") or doc.get("path"),
                "topic": doc.get("topic"),
                "similarity": doc.get("similarity"),
                "chunk_index": doc.get("chunk_index"),
                "start_line": doc.get("start_line"),
                "end_line": doc.get("end_line"),
            }
            content = doc.get("content", "")
            if content:
                item["content_preview"] = content[:500].replace("\n", " ")
            items.append(item)
        return {"result_count": len(data), "items": items}
    if isinstance(data, dict):
        content = data.get("content", "")
        return {
            "doc_id": data.get("doc_id"),
            "file": data.get("file") or data.get("path"),
            "topic": data.get("topic"),
            "content_preview": content[:max_content_len] if content else None,
        }
    return {"raw_preview": str(data)[:max_content_len]}


def parse_fog_log(path: Path) -> list[dict]:
    text = path.read_text(errors="replace")
    lines = text.splitlines()

    entries = []
    i = 0
    while i < len(lines):
        line = lines[i]
        rag_calls = extract_tool_calls_from_llm(line)
        if not rag_calls:
            i += 1
            continue

        for call in rag_calls:
            tool = call["tool"]
            args = call["arguments"]
            query = args.get("query", args.get("input", args.get("doc_id", "")))
            if isinstance(query, list):
                query = " ".join(str(x) for x in query)

            entry = {
                "tool": tool,
                "query": query,
                "arguments": {k: v for k, v in args.items() if k != "query" and k != "input"},
                "result": None,
                "result_summary": None,
            }

            j = i + 1
            in_result = False
            result_lines = []
            while j < len(lines):
                l = lines[j]
                if "[TOOL RESULT]" in l:
                    block = "\n".join(lines[j : j + 6])
                    if tool in block:
                        in_result = True
                        j += 1
                        skip_until_result = True
                        while j < len(lines) and skip_until_result:
                            if "Result:" in lines[j]:
                                j += 1
                                skip_until_result = False
                            else:
                                j += 1
                        continue
                if in_result:
                    if "└" in l and "──" in l:
                        break
                    if "[TOOL CALL]" in l or ("[TOOL RESULT]" in l and j > i + 5):
                        break
                    cleaned = strip_box_line(l)
                    if cleaned and "Result:" not in cleaned and "Tool:" not in cleaned and "Chain:" not in cleaned:
                        result_lines.append(cleaned)
                j += 1

            if result_lines:
                raw_result = "\n".join(result_lines)
                try:
                    parsed = json.loads(raw_result)
                    entry["result"] = parsed
                    entry["result_summary"] = summarize_rag_result(raw_result, tool)
                except json.JSONDecodeError:
                    entry["result_raw"] = raw_result[:3000]
                    entry["result_summary"] = summarize_rag_result(raw_result, tool)

            entries.append(entry)
        i += 1

    return entries


def main():
    parser = argparse.ArgumentParser(
        description="Parse RAG questions and results from FoG agent .ans log files"
    )
    parser.add_argument(
        "log_file",
        type=Path,
        nargs="?",
        default=Path("Sources/Agentic_System/agents/fog_logs/rises_the_fog33.ans"),
        help="Path to .ans log file",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Output JSON file (default: print to stdout)",
    )
    parser.add_argument(
        "-c", "--compact",
        action="store_true",
        help="Compact output: only query and result summary, no full result",
    )
    parser.add_argument(
        "--no-result",
        action="store_true",
        help="Only output queries, skip result data",
    )
    args = parser.parse_args()

    log_path = args.log_file
    if not log_path.is_absolute():
        log_path = Path(__file__).resolve().parent.parent / log_path
    if not log_path.exists():
        print(f"Error: file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    entries = parse_fog_log(log_path)

    if args.no_result:
        out = [{"tool": e["tool"], "query": e["query"], "arguments": e["arguments"]} for e in entries]
    elif args.compact:
        out = [
            {
                "tool": e["tool"],
                "query": e["query"],
                "result_summary": e.get("result_summary"),
            }
            for e in entries
        ]
    else:
        out = entries

    json_str = json.dumps(out, indent=2, default=str)

    if args.output:
        args.output.write_text(json_str)
        print(f"Wrote {len(entries)} RAG entries to {args.output}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
