from typing import Optional, Any
import os
import tiktoken
from tree_sitter import Language, Parser, Query, QueryCursor
import tree_sitter_python
import tree_sitter_javascript
import tree_sitter_typescript
import tree_sitter_java

tokenizer = tiktoken.get_encoding("cl100k_base")

def get_token_count(text: str) -> int:
    return len(tokenizer.encode(text))

def get_language_from_filepath(filepath: str) -> Optional[tuple[str, Language]]:
    _, ext = os.path.splitext(filepath)
    lang_map = {
        ".py": ("python", tree_sitter_python.language()),
        ".js": ("javascript", tree_sitter_javascript.language()),
        ".jsx": ("javascript", tree_sitter_javascript.language()),
        ".ts": ("typescript", tree_sitter_typescript.language_typescript()),
        ".tsx": ("tsx", tree_sitter_typescript.language_tsx()),
        ".java": ("java", tree_sitter_java.language()),
    }
    if ext not in lang_map:
        return None
    lang_name, lang_ptr = lang_map[ext]
    return lang_name, Language(lang_ptr)


def get_query_pattern_for_language(lang_name: str) -> str:
    """Returns the Tree-sitter S-expression query for extracting classes and functions."""
    if lang_name == "python":
        return """
        (class_definition name: (identifier) @class) @class_body
        (function_definition name: (identifier) @function) @function_body
        """
    elif lang_name == "java":
        return """
        (class_declaration name: (identifier) @class) @class_body
        (interface_declaration name: (identifier) @class) @class_body
        (record_declaration name: (identifier) @class) @class_body
        (method_declaration name: (identifier) @function) @function_body
        (constructor_declaration name: (identifier) @function) @function_body
        """
    return """
    (class_declaration name: (identifier) @class) @class_body
    (function_declaration name: (identifier) @function) @function_body
    (method_definition name: (property_identifier) @function) @function_body
    (variable_declarator name: (identifier) @function value: (arrow_function)) @function_body
    """

def parse_code_to_captures(code: str, filepath: str) -> Optional[dict[str, list[Any]]]:
    """Parses code into an AST and executes the Tree-sitter query cursor."""
    lang_info = get_language_from_filepath(filepath)
    if not lang_info:
        return None
    lang_name, language = lang_info
    try:
        parser = Parser(language)
        code_bytes = bytes(code, "utf8")
        tree = parser.parse(code_bytes)
        query_str = get_query_pattern_for_language(lang_name)
        query = Query(language, query_str)
        cursor = QueryCursor(query)
        return cursor.captures(tree.root_node)
    except Exception as e:
        print(f"Failed to parse AST for {filepath}: {e}")
        return None


def create_block_from_ast_node(lines: list[str], node: Any, tag: str, filepath: str) -> dict:
    """Extracts start line, end line, and code slice for an individual AST node."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    block_code = "\n".join(lines[start_line - 1 : end_line])
    symbol_type = "Class" if tag == "class_body" else "Function"
    return {
        "filepath": filepath,
        "symbol_type": symbol_type,
        "start_line": start_line,
        "end_line": end_line,
        "code": block_code,
    }

def extract_ast_code_blocks(code: str, filepath: str) -> list[dict]:
    """Orchestrates AST parsing and returns all class/function code blocks."""
    captures = parse_code_to_captures(code, filepath)
    if not captures or not isinstance(captures, dict):
        return []
    lines = code.splitlines()
    blocks = []
    for tag, nodes in captures.items():
        if tag in ("class_body", "function_body"):
            for node in nodes:
                block = create_block_from_ast_node(lines, node, tag, filepath)
                blocks.append(block)
    return blocks

def format_chunk_header(filepath: str, symbol_type: str, start_line: int, end_line: int) -> str:
    """Formats contextual metadata header to prepend to code chunks."""
    return (
        f"File: {filepath}\n"
        f"Type: {symbol_type}\n"
        f"Lines: {start_line}-{end_line}\n"
        f"----------------------------------------\n"
    )


def split_oversized_code_with_bpe(code: str, max_tokens: int = 512, overlap: int = 64) -> list[str]:
    """Splits a single large function into overlapping token slices."""
    tokens = tokenizer.encode(code)
    slices = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        slices.append(tokenizer.decode(tokens[start:end]))
        if end == len(tokens):
            break
        start += max_tokens - overlap
    return slices



def process_single_block_guardrails(
    block: dict,
    min_tokens: int = 15,
    max_tokens: int = 512,
    overlap: int = 64
) -> list[dict]:
    """Applies min/max token guardrails and formats headers for a single code block."""
    token_count = get_token_count(block["code"])
    # 1. Floor guardrail: ignore trivial 1-line stubs
    if token_count < min_tokens:
        return []
    header = format_chunk_header(
        block["filepath"], block["symbol_type"], block["start_line"], block["end_line"]
    )
    # 2. Normal range: Keep intact
    if token_count <= max_tokens:
        return [{
            "filepath": block["filepath"],
            "start_line": block["start_line"],
            "end_line": block["end_line"],
            "symbol_type": block["symbol_type"],
            "text": header + block["code"],
            "token_count": token_count,
        }]
    # 3. Ceiling guardrail: Split oversized function
    slices = split_oversized_code_with_bpe(block["code"], max_tokens, overlap)
    chunks = []
    for i, slice_code in enumerate(slices, start=1):
        chunks.append({
            "filepath": block["filepath"],
            "start_line": block["start_line"],
            "end_line": block["end_line"],
            "symbol_type": f"{block['symbol_type']} (Part {i})",
            "text": header + slice_code,
            "token_count": get_token_count(slice_code),
        })
    return chunks


def chunk_with_bpe_guardrails(
    code_blocks: list[dict],
    min_tokens: int = 15,
    max_tokens: int = 512,
    overlap: int = 64
) -> list[dict]:
    """Orchestrates applying BPE guardrails over all extracted AST blocks."""
    final_chunks = []
    for block in code_blocks:
        processed_chunks = process_single_block_guardrails(block, min_tokens, max_tokens, overlap)
        final_chunks.extend(processed_chunks)
    return final_chunks