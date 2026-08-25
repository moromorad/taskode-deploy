from unittest.mock import patch
import pytest

from coresite.services.rag_chunker import (
    chunk_with_bpe_guardrails,
    create_block_from_ast_node,
    extract_ast_code_blocks,
    format_chunk_header,
    get_language_from_filepath,
    get_query_pattern_for_language,
    get_token_count,
    parse_code_to_captures,
    process_single_block_guardrails,
    split_oversized_code_with_bpe,
)


# ==========================================
# 1. Token Helpers Tests
# ==========================================

def test_get_token_count():
    assert get_token_count("") == 0
    count = get_token_count("def hello_world():\n    return 'hello'")
    assert count > 0
    assert isinstance(count, int)


# ==========================================
# 2. Tree-sitter Language & Query Pattern Tests
# ==========================================

def test_get_language_from_filepath_supported():
    for ext in [".py", ".js", ".jsx", ".ts", ".tsx", ".java"]:
        res = get_language_from_filepath(f"app/file{ext}")
        assert res is not None
        lang_name, lang_obj = res
        assert isinstance(lang_name, str)


def test_get_language_from_filepath_unsupported():
    assert get_language_from_filepath("readme.md") is None
    assert get_language_from_filepath("style.css") is None
    assert get_language_from_filepath("data.json") is None


def test_get_query_pattern_for_language():
    py_pattern = get_query_pattern_for_language("python")
    assert "class_definition" in py_pattern
    assert "function_definition" in py_pattern

    java_pattern = get_query_pattern_for_language("java")
    assert "class_declaration" in java_pattern
    assert "method_declaration" in java_pattern

    js_pattern = get_query_pattern_for_language("javascript")
    assert "class_declaration" in js_pattern
    assert "arrow_function" in js_pattern


# ==========================================
# 3. AST Extraction Tests
# ==========================================

def test_parse_code_to_captures_unsupported_language():
    assert parse_code_to_captures("print('hello')", "file.txt") is None


def test_parse_code_to_captures_exception():
    with patch("coresite.services.rag_chunker.Parser", side_effect=Exception("Parser crashed")):
        assert parse_code_to_captures("def foo(): pass", "file.py") is None


def test_extract_ast_code_blocks_python():
    py_code = """class UserManager:
    def get_user(self, user_id):
        return User.objects.get(id=user_id)

def calculate_tax(amount):
    rate = 0.15
    return amount * rate
"""
    blocks = extract_ast_code_blocks(py_code, "services/user.py")
    assert len(blocks) >= 2

    # Check for Class
    class_block = next((b for b in blocks if b["symbol_type"] == "Class"), None)
    assert class_block is not None
    assert "class UserManager" in class_block["code"]
    assert class_block["start_line"] == 1
    assert class_block["filepath"] == "services/user.py"

    # Check for Function
    func_block = next((b for b in blocks if "calculate_tax" in b["code"]), None)
    assert func_block is not None
    assert func_block["symbol_type"] == "Function"
    assert "return amount * rate" in func_block["code"]


def test_extract_ast_code_blocks_javascript():
    js_code = """class AuthController {
    login() {
        return true;
    }
}

const sendNotification = (userId) => {
    console.log(userId);
};
"""
    blocks = extract_ast_code_blocks(js_code, "src/auth.js")
    assert len(blocks) >= 2
    types = [b["symbol_type"] for b in blocks]
    assert "Class" in types
    assert "Function" in types


def test_extract_ast_code_blocks_java():
    java_code = """public class PaymentProcessor {
    public boolean processTransaction(double amount) {
        return amount > 0;
    }
}
"""
    blocks = extract_ast_code_blocks(java_code, "src/PaymentProcessor.java")
    assert len(blocks) >= 2
    types = [b["symbol_type"] for b in blocks]
    assert "Class" in types
    assert "Function" in types
    assert any("PaymentProcessor" in b["code"] for b in blocks)
    assert any("processTransaction" in b["code"] for b in blocks)


def test_extract_ast_code_blocks_unsupported_or_empty():
    assert extract_ast_code_blocks("plain text", "notes.txt") == []
    assert extract_ast_code_blocks("", "empty.py") == []


# ==========================================
# 4. Context Header & Guardrails Tests
# ==========================================

def test_format_chunk_header():
    header = format_chunk_header("coresite/models.py", "Class", 10, 25)
    assert "File: coresite/models.py" in header
    assert "Type: Class" in header
    assert "Lines: 10-25" in header
    assert "----------------------------------------" in header


def test_split_oversized_code_with_bpe():
    # Generate long code block (~100 tokens)
    long_code = "x = 1\n" * 50
    # Split with max_tokens = 20 and overlap = 5
    slices = split_oversized_code_with_bpe(long_code, max_tokens=20, overlap=5)
    assert len(slices) > 1
    for s in slices:
        assert len(s) > 0


def test_process_single_block_guardrails_floor_discarded():
    # Trivial stub under 15 tokens
    stub_block = {
        "filepath": "app/views.py",
        "symbol_type": "Function",
        "start_line": 1,
        "end_line": 2,
        "code": "def stub():\n    pass",
    }
    chunks = process_single_block_guardrails(stub_block, min_tokens=15)
    assert chunks == []


def test_process_single_block_guardrails_normal_range():
    # Medium function between 15 and 512 tokens
    normal_block = {
        "filepath": "app/views.py",
        "symbol_type": "Function",
        "start_line": 10,
        "end_line": 18,
        "code": "def calculate_total(items):\n    total = 0\n    for item in items:\n        total += item.price * item.quantity\n    return total",
    }
    chunks = process_single_block_guardrails(normal_block, min_tokens=10, max_tokens=512)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["filepath"] == "app/views.py"
    assert "File: app/views.py" in chunk["text"]
    assert "def calculate_total" in chunk["text"]
    assert chunk["token_count"] > 10


def test_process_single_block_guardrails_oversized_ceiling():
    # Create an oversized code block
    huge_code = "def massive_function():\n" + "    data = [i * 2 for i in range(100)]\n" * 20
    oversized_block = {
        "filepath": "app/heavy.py",
        "symbol_type": "Function",
        "start_line": 1,
        "end_line": 25,
        "code": huge_code,
    }
    # Force max_tokens = 30 to trigger ceiling split
    chunks = process_single_block_guardrails(oversized_block, min_tokens=5, max_tokens=30, overlap=5)
    assert len(chunks) > 1
    assert "Part 1" in chunks[0]["symbol_type"]
    assert "Part 2" in chunks[1]["symbol_type"]
    for c in chunks:
        assert "File: app/heavy.py" in c["text"]


def test_chunk_with_bpe_guardrails_orchestrator():
    blocks = [
        # Stub (< 15 tokens) -> should be filtered out
        {
            "filepath": "app/a.py",
            "symbol_type": "Function",
            "start_line": 1,
            "end_line": 2,
            "code": "def noop(): pass",
        },
        # Normal block
        {
            "filepath": "app/b.py",
            "symbol_type": "Function",
            "start_line": 5,
            "end_line": 12,
            "code": "def process_order(order_id):\n    order = Order.objects.get(id=order_id)\n    order.status = 'processed'\n    order.save()\n    return order",
        },
    ]
    final_chunks = chunk_with_bpe_guardrails(blocks, min_tokens=10, max_tokens=512)
    assert len(final_chunks) == 1
    assert final_chunks[0]["filepath"] == "app/b.py"
