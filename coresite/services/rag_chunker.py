from dataclasses import dataclass
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

def get_enclosing_class_signature(block: dict, class_blocks: list[dict]) -> Optional[str]:
    """Finds the enclosing class for a given block and extracts its declaration signature."""
    if not class_blocks or not block:
        return None
    b_start = block.get("start_line", 0)
    b_end = block.get("end_line", 0)
    for cls in class_blocks:
        if cls.get("start_line", 0) <= b_start and cls.get("end_line", 0) >= b_end:
            for line in cls.get("code", "").splitlines():
                line_str = line.strip()
                # Skip decorator annotations / empty lines to find the class declaration signature
                if line_str.startswith((
                    "class ", "public class ", "protected class ", "private class ",
                    "interface ", "public interface ", "record ", "public record ",
                    "abstract class ", "public abstract class "
                )):
                    return line_str
            # Fallback to first non-empty line of the class
            for line in cls.get("code", "").splitlines():
                if line.strip():
                    return line.strip()
    return None


def format_chunk_header(
    filepath: str,
    symbol_type: str,
    start_line: int,
    end_line: int,
    enclosing_class: Optional[str] = None,
) -> str:
    """Formats contextual metadata header to prepend to code chunks."""
    header_parts = [f"File: {filepath}"]
    if enclosing_class:
        header_parts.append(f"Class: {enclosing_class}")
    header_parts.append(f"Type: {symbol_type}")
    header_parts.append(f"Lines: {start_line}-{end_line}")
    header_parts.append("----------------------------------------\n")
    return "\n".join(header_parts)


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



@dataclass(frozen=True)
class ChunkingConfig:
    """
    Elastic AST chunking boundaries for balanced semantic cohesion and packing density.
    All soft limits include predefined elastic windows to respect natural code syntax.
    """
    target_tokens: int = 300       # Nominal chunk target
    target_tolerance: int = 50     # Soft packing range: [250, 350] tokens
    max_intact_tokens: int = 650   # Nominal ceiling for intact functions / classes
    intact_tolerance: int = 100    # Elastic upper limit for intact code: up to 750 tokens
    max_stitch_ceiling: int = 750  # Elastic ceiling for tail stitching
    bpe_slice_size: int = 512      # Fixed window for massive algorithmic splitting
    bpe_overlap: int = 64          # Continuity overlap between slices
    model_hard_limit: int = 2048   # Absolute Gemini embedding token barrier

    @property
    def soft_pack_min(self) -> int:
        """Lower threshold where a pack is considered full enough to stand on its own (250)."""
        return self.target_tokens - self.target_tolerance

    @property
    def soft_pack_max(self) -> int:
        """Upper threshold for normal method accumulation (350)."""
        return self.target_tokens + self.target_tolerance

    @property
    def elastic_intact_ceiling(self) -> int:
        """Upper elastic threshold where an intact class or function stays unbroken (750)."""
        return self.max_intact_tokens + self.intact_tolerance

    def should_keep_class_intact(self, cls_tokens: int) -> bool:
        """Returns True if the entire class can be preserved as 1 chunk."""
        return cls_tokens <= self.elastic_intact_ceiling

    def is_oversized_function(self, func_tokens: int) -> bool:
        """Returns True if a function exceeds the elastic ceiling and must be sliced with BPE."""
        return func_tokens > self.elastic_intact_ceiling

    def can_absorb_into_pack(self, current_tokens: int, block_tokens: int) -> bool:
        """
        Determines if an incoming block should be absorbed into current_pack:
        1. Fits within soft_pack_max (<= 350)
        2. OR current pack is undersized (< 250) and combined fits in elastic ceiling (<= 750)
        """
        combined = current_tokens + block_tokens
        if combined <= self.soft_pack_max:
            return True
        if current_tokens < self.soft_pack_min and combined <= self.elastic_intact_ceiling:
            return True
        return False

    def can_tail_stitch(self, prev_tokens: int, leftover_tokens: int) -> bool:
        """Returns True if leftover buffer at EOF can be safely stitched to the previous chunk."""
        return (prev_tokens + leftover_tokens) <= self.max_stitch_ceiling


def pack_file_ast_blocks(
    blocks: list[dict],
    target_tokens: int = 300,
    tolerance: int = 50,
    max_tokens: int = 650,
    intact_tolerance: int = 100,
    max_stitch_ceiling: int = 750,
    overlap: int = 64,
    config: Optional[ChunkingConfig] = None,
) -> list[dict]:
    """
    Zero-loss AST chunk packing algorithm with elastic soft limits and Tail Stitching.
    - Preserves 100% of all code blocks (zero loss).
    - Uses ChunkingConfig to regulate elastic boundaries.
    - Keeps classes/functions up to 750 tokens intact.
    - Preserves enclosing class declaration headers in method chunks.
    - Slices genuine oversized algorithms (> 750 tokens) with BPE overlap.
    """
    if not blocks:
        return []

    if config is None:
        config = ChunkingConfig(
            target_tokens=target_tokens,
            target_tolerance=tolerance,
            max_intact_tokens=max_tokens,
            intact_tolerance=intact_tolerance,
            max_stitch_ceiling=max_stitch_ceiling,
            bpe_overlap=overlap,
        )

    # Sort blocks by start_line
    sorted_blocks = sorted(blocks, key=lambda b: (b.get("start_line", 0), -b.get("end_line", 0)))
    
    # 1. Check if there is a single class block that fits within the elastic ceiling
    class_blocks = [b for b in sorted_blocks if b.get("symbol_type") == "Class"]
    if len(class_blocks) == 1 and len(sorted_blocks) > 1:
        cls_block = class_blocks[0]
        cls_tokens = get_token_count(cls_block["code"])
        if config.should_keep_class_intact(cls_tokens):
            header = format_chunk_header(
                cls_block["filepath"], "Class", cls_block["start_line"], cls_block["end_line"]
            )
            return [{
                "filepath": cls_block["filepath"],
                "start_line": cls_block["start_line"],
                "end_line": cls_block["end_line"],
                "symbol_type": "Class",
                "text": header + cls_block["code"],
                "token_count": cls_tokens,
            }]

    # Filter out duplicate enclosing class blocks if we are chunking by individual methods
    method_blocks = [b for b in sorted_blocks if b.get("symbol_type") != "Class"]
    if not method_blocks:
        method_blocks = sorted_blocks

    packed_chunks = []
    current_pack = []
    current_tokens = 0

    def flush_pack():
        nonlocal current_pack, current_tokens
        if not current_pack:
            return
        first_b = current_pack[0]
        last_b = current_pack[-1]
        combined_code = "\n\n".join(b["code"] for b in current_pack)
        types_summary = " & ".join(dict.fromkeys(b["symbol_type"] for b in current_pack))
        enclosing_cls = get_enclosing_class_signature(first_b, class_blocks)
        header = format_chunk_header(
            first_b["filepath"],
            types_summary if len(current_pack) == 1 else f"{types_summary} (Group)",
            first_b["start_line"],
            last_b["end_line"],
            enclosing_class=enclosing_cls,
        )
        total_tokens = get_token_count(combined_code)
        packed_chunks.append({
            "filepath": first_b["filepath"],
            "start_line": first_b["start_line"],
            "end_line": last_b["end_line"],
            "symbol_type": types_summary if len(current_pack) == 1 else f"{types_summary} (Group)",
            "text": header + combined_code,
            "token_count": total_tokens,
        })
        current_pack = []
        current_tokens = 0

    for block in method_blocks:
        b_tokens = get_token_count(block["code"])

        # Case 1: Oversized function (exceeds elastic ceiling > 750)
        # Always isolate massive algorithms into their own clean boundary
        if config.is_oversized_function(b_tokens):
            if current_pack:
                flush_pack()

            enclosing_cls = get_enclosing_class_signature(block, class_blocks)
            header = format_chunk_header(
                block["filepath"],
                block["symbol_type"],
                block["start_line"],
                block["end_line"],
                enclosing_class=enclosing_cls,
            )
            slices = split_oversized_code_with_bpe(
                block["code"], max_tokens=config.bpe_slice_size, overlap=config.bpe_overlap
            )
            for i, slice_code in enumerate(slices, start=1):
                packed_chunks.append({
                    "filepath": block["filepath"],
                    "start_line": block["start_line"],
                    "end_line": block["end_line"],
                    "symbol_type": f"{block['symbol_type']} (Part {i})",
                    "text": header + slice_code,
                    "token_count": get_token_count(slice_code),
                })
            continue

        # Case 2: Standard method accumulation with soft limit helper
        if not current_pack:
            current_pack.append(block)
            current_tokens = b_tokens
        elif config.can_absorb_into_pack(current_tokens, b_tokens):
            current_pack.append(block)
            current_tokens += b_tokens
        else:
            flush_pack()
            current_pack.append(block)
            current_tokens = b_tokens

    # Case 3: End-of-File Tail Stitching with helper
    if current_pack:
        leftover_code = "\n\n".join(b["code"] for b in current_pack)
        leftover_tokens = get_token_count(leftover_code)

        if packed_chunks and config.can_tail_stitch(packed_chunks[-1]["token_count"], leftover_tokens):
            prev = packed_chunks[-1]
            prev["end_line"] = current_pack[-1]["end_line"]
            prev["text"] += "\n\n" + leftover_code
            prev["token_count"] += leftover_tokens
            current_pack = []
            current_tokens = 0
        else:
            flush_pack()

    return packed_chunks


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
    target_tokens: int = 300,
    tolerance: int = 50,
    max_tokens: int = 650,
    intact_tolerance: int = 100,
    max_stitch_ceiling: int = 750,
    overlap: int = 64,
    config: Optional[ChunkingConfig] = None,
) -> list[dict]:
    """
    Orchestrates AST chunk packing and BPE token guardrails across all code files.
    Groups code blocks by filepath to pack small contiguous methods together into ~300-token chunks.
    """
    if not code_blocks:
        return []

    if config is None:
        config = ChunkingConfig(
            target_tokens=target_tokens,
            target_tolerance=tolerance,
            max_intact_tokens=max_tokens,
            intact_tolerance=intact_tolerance,
            max_stitch_ceiling=max_stitch_ceiling,
            bpe_overlap=overlap,
        )

    # Group blocks by file path
    blocks_by_file: dict[str, list[dict]] = {}
    for block in code_blocks:
        fp = block.get("filepath", "")
        if fp not in blocks_by_file:
            blocks_by_file[fp] = []
        blocks_by_file[fp].append(block)

    final_chunks = []
    for fp, file_blocks in blocks_by_file.items():
        file_chunks = pack_file_ast_blocks(
            file_blocks,
            config=config,
        )
        final_chunks.extend(file_chunks)

    return final_chunks