from unittest.mock import patch
import pytest

from coresite.services.rag_chunker import (
    ChunkingConfig,
    chunk_with_bpe_guardrails,
    create_block_from_ast_node,
    extract_ast_code_blocks,
    format_chunk_header,
    get_enclosing_class_signature,
    get_language_from_filepath,
    get_query_pattern_for_language,
    get_token_count,
    pack_file_ast_blocks,
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
        # Small function in a.py
        {
            "filepath": "app/a.py",
            "symbol_type": "Function",
            "start_line": 1,
            "end_line": 2,
            "code": "def noop(): pass",
        },
        # Normal block in b.py
        {
            "filepath": "app/b.py",
            "symbol_type": "Function",
            "start_line": 5,
            "end_line": 12,
            "code": "def process_order(order_id):\n    order = Order.objects.get(id=order_id)\n    order.status = 'processed'\n    order.save()\n    return order",
        },
    ]
    # Zero-loss guarantees both files have their code preserved as chunks
    final_chunks = chunk_with_bpe_guardrails(blocks, target_tokens=300, tolerance=50)
    assert len(final_chunks) == 2
    assert final_chunks[0]["filepath"] == "app/a.py"
    assert final_chunks[1]["filepath"] == "app/b.py"


def test_pack_file_ast_blocks_single_class_preservation():
    blocks = [
        {
            "filepath": "src/Card.java",
            "symbol_type": "Class",
            "start_line": 1,
            "end_line": 30,
            "code": "public class Card {\n    private int suit;\n    public int getSuit() { return suit; }\n}",
        },
        {
            "filepath": "src/Card.java",
            "symbol_type": "Function",
            "start_line": 3,
            "end_line": 3,
            "code": "public int getSuit() { return suit; }",
        },
    ]
    chunks = pack_file_ast_blocks(blocks, target_tokens=300, tolerance=50, max_tokens=600)
    assert len(chunks) == 1
    assert chunks[0]["symbol_type"] == "Class"
    assert "public class Card" in chunks[0]["text"]


def test_pack_file_ast_blocks_combines_small_methods():
    blocks = [
        {
            "filepath": "src/Deck.java",
            "symbol_type": "Function",
            "start_line": 10,
            "end_line": 15,
            "code": "public void shuffle() {\n    Collections.shuffle(cards);\n}",
        },
        {
            "filepath": "src/Deck.java",
            "symbol_type": "Function",
            "start_line": 16,
            "end_line": 20,
            "code": "public Card draw() {\n    return cards.remove(0);\n}",
        },
        {
            "filepath": "src/Deck.java",
            "symbol_type": "Function",
            "start_line": 21,
            "end_line": 25,
            "code": "public int size() {\n    return cards.size();\n}",
        },
    ]
    # Small methods (< target_tokens 300) are packed together into 1 cohesive chunk
    chunks = pack_file_ast_blocks(blocks, target_tokens=300, tolerance=50, max_tokens=600)
    assert len(chunks) == 1
    assert "shuffle" in chunks[0]["text"]
    assert "draw" in chunks[0]["text"]
    assert "size" in chunks[0]["text"]
    assert chunks[0]["start_line"] == 10
    assert chunks[0]["end_line"] == 25


def test_pack_file_ast_blocks_zero_loss_small_preceding_large():
    blocks = [
        # Small 15-token function
        {
            "filepath": "src/Service.java",
            "symbol_type": "Function",
            "start_line": 1,
            "end_line": 3,
            "code": "public boolean isValid() { return status == 1; }",
        },
        # Medium 280-token function
        {
            "filepath": "src/Service.java",
            "symbol_type": "Function",
            "start_line": 5,
            "end_line": 30,
            "code": "public void processBatch() {\n" + "    System.out.println(\"Processing batch item\");\n" * 15 + "}",
        },
    ]
    # Small function preceding medium function is preserved and merged into the same chunk (<= 350 soft max)
    chunks = pack_file_ast_blocks(blocks, target_tokens=300, tolerance=50, max_tokens=600)
    assert len(chunks) == 1
    assert "isValid" in chunks[0]["text"]
    assert "processBatch" in chunks[0]["text"]


def test_chunk_with_bpe_guardrails_multi_file_isolation():
    blocks = [
        {
            "filepath": "src/FileA.java",
            "symbol_type": "Function",
            "start_line": 1,
            "end_line": 5,
            "code": "public void methodA() { System.out.println(\"A\"); }",
        },
        {
            "filepath": "src/FileB.java",
            "symbol_type": "Function",
            "start_line": 1,
            "end_line": 5,
            "code": "public void methodB() { System.out.println(\"B\"); }",
        },
    ]
    chunks = chunk_with_bpe_guardrails(blocks, target_tokens=300, tolerance=50)
    assert len(chunks) == 2
    assert chunks[0]["filepath"] == "src/FileA.java"
    assert chunks[1]["filepath"] == "src/FileB.java"


def test_pack_file_ast_blocks_end_of_file_tail_stitching():
    blocks = [
        # Chunk 1 (300 tokens)
        {
            "filepath": "src/OrderService.java",
            "symbol_type": "Function",
            "start_line": 1,
            "end_line": 20,
            "code": "public void processOrder() {\n" + "    validateItem();\n" * 25 + "}",
        },
        # Leftover function at end of file (20 tokens)
        {
            "filepath": "src/OrderService.java",
            "symbol_type": "Function",
            "start_line": 22,
            "end_line": 25,
            "code": "public boolean isComplete() { return true; }",
        },
    ]
    # The leftover 20-token function at EOF should be stitched to Chunk 1 tail
    chunks = pack_file_ast_blocks(blocks, target_tokens=300, tolerance=50, max_tokens=600, max_stitch_ceiling=700)
    assert len(chunks) == 1
    assert "processOrder" in chunks[0]["text"]
    assert "isComplete" in chunks[0]["text"]
    assert chunks[0]["end_line"] == 25


def test_pack_file_ast_blocks_pre_massive_function_isolation():
    massive_code = "public void massiveAlgorithm() {\n" + "    complexStep();\n" * 250 + "}"
    blocks = [
        # Small helper (15 tokens)
        {
            "filepath": "src/MathEngine.java",
            "symbol_type": "Function",
            "start_line": 1,
            "end_line": 3,
            "code": "public double getPi() { return 3.14159; }",
        },
        # 1000-token massive algorithm
        {
            "filepath": "src/MathEngine.java",
            "symbol_type": "Function",
            "start_line": 5,
            "end_line": 260,
            "code": massive_code,
        },
    ]
    chunks = pack_file_ast_blocks(blocks, target_tokens=300, tolerance=50, max_tokens=500)
    assert len(chunks) >= 3
    # Chunk 0 is the clean standalone helper
    assert "getPi" in chunks[0]["text"]
    assert "massiveAlgorithm" not in chunks[0]["text"]
    # Chunk 1 is Part 1 of the massive algorithm
    assert "massiveAlgorithm" in chunks[1]["text"]
    assert "getPi" not in chunks[1]["text"]
    assert "Part 1" in chunks[1]["symbol_type"]


def test_pack_file_ast_blocks_tail_stitching_exceeds_ceiling_emits_separate():
    # Chunk 1 (140 lines -> ~560 tokens)
    code_550 = "public void hugeTask1() {\n" + "    runJob1();\n" * 140 + "}"
    # Leftover (70 lines -> ~280 tokens) -> 560 + 280 = 840 > 700 ceiling
    code_200 = "public void hugeTask2() {\n" + "    runJob2();\n" * 70 + "}"
    blocks = [
        {
            "filepath": "src/Jobs.java",
            "symbol_type": "Function",
            "start_line": 1,
            "end_line": 145,
            "code": code_550,
        },
        {
            "filepath": "src/Jobs.java",
            "symbol_type": "Function",
            "start_line": 150,
            "end_line": 220,
            "code": code_200,
        },
    ]
    chunks = pack_file_ast_blocks(blocks, target_tokens=300, tolerance=50, max_tokens=600, max_stitch_ceiling=700)
    assert len(chunks) == 2
    assert chunks[0]["token_count"] > 250
    assert chunks[1]["token_count"] > 150


def test_pack_file_ast_blocks_pre_massive_function_full_chunk_emits_standalone():
    # Preceding function is already 300 tokens (>= 250 soft_min)
    func_300 = "public void fullFeature() {\n" + "    featureStep();\n" * 70 + "}"
    massive_code = "public void massiveAlgorithm() {\n" + "    stepOperation();\n" * 250 + "}"
    blocks = [
        {
            "filepath": "src/Engine.java",
            "symbol_type": "Function",
            "start_line": 1,
            "end_line": 75,
            "code": func_300,
        },
        {
            "filepath": "src/Engine.java",
            "symbol_type": "Function",
            "start_line": 80,
            "end_line": 340,
            "code": massive_code,
        },
    ]
    chunks = pack_file_ast_blocks(blocks, target_tokens=300, tolerance=50, max_tokens=500)
    # Chunk 0 should be fullFeature standalone, and subsequent chunks should be parts of massiveAlgorithm
    assert len(chunks) >= 3
    assert "fullFeature" in chunks[0]["text"]
    assert "massiveAlgorithm" not in chunks[0]["text"]
    assert "massiveAlgorithm" in chunks[1]["text"]


def test_chunking_config_elastic_boundaries():
    cfg = ChunkingConfig(
        target_tokens=300,
        target_tolerance=50,
        max_intact_tokens=650,
        intact_tolerance=100,
        max_stitch_ceiling=750,
    )
    assert cfg.soft_pack_min == 250
    assert cfg.soft_pack_max == 350
    assert cfg.elastic_intact_ceiling == 750

    # 680-token class should stay intact because 680 <= 750
    assert cfg.should_keep_class_intact(680) is True
    # 800-token class exceeds elastic ceiling
    assert cfg.should_keep_class_intact(800) is False

    # 800-token function is oversized
    assert cfg.is_oversized_function(800) is True
    assert cfg.is_oversized_function(680) is False

    # Can absorb test
    assert cfg.can_absorb_into_pack(100, 150) is True  # 250 <= 350
    assert cfg.can_absorb_into_pack(200, 400) is True  # 200 < 250 and 600 <= 750
    assert cfg.can_absorb_into_pack(300, 100) is False # 300 >= 250 and 400 > 350

    # Can tail stitch test
    assert cfg.can_tail_stitch(300, 50) is True   # 350 <= 750
    assert cfg.can_tail_stitch(500, 300) is False # 800 > 750


def test_format_chunk_header_with_enclosing_class():
    header = format_chunk_header(
        filepath="src/Service.java",
        symbol_type="Function",
        start_line=10,
        end_line=25,
        enclosing_class="public class Service implements IService",
    )
    assert "File: src/Service.java" in header
    assert "Class: public class Service implements IService" in header
    assert "Type: Function" in header
    assert "Lines: 10-25" in header


def test_get_enclosing_class_signature():
    class_blocks = [
        {
            "filepath": "app/views.py",
            "symbol_type": "Class",
            "start_line": 1,
            "end_line": 100,
            "code": "@permission_classes([IsAuthenticated])\nclass TaskViewSet(ModelViewSet):\n    queryset = Task.objects.all()",
        }
    ]
    method_block = {
        "filepath": "app/views.py",
        "symbol_type": "Function",
        "start_line": 20,
        "end_line": 40,
        "code": "def list(self, request): return Response([])",
    }
    sig = get_enclosing_class_signature(method_block, class_blocks)
    assert sig == "class TaskViewSet(ModelViewSet):"


def test_pack_file_ast_blocks_preserves_enclosing_class_context_in_methods():
    # Huge class (> 750 tokens) that gets broken into method chunks
    huge_class_code = "public class PaymentGateway implements IPayment {\n" + "    private int config = 1;\n" * 200 + "}"
    method1_code = "public void chargeCreditCard() {\n    System.out.println(\"Charging\");\n}"
    method2_code = "public void refundPayment() {\n    System.out.println(\"Refunding\");\n}"

    blocks = [
        # Class block (oversized > 750 tokens)
        {
            "filepath": "src/PaymentGateway.java",
            "symbol_type": "Class",
            "start_line": 1,
            "end_line": 250,
            "code": huge_class_code,
        },
        # Method 1
        {
            "filepath": "src/PaymentGateway.java",
            "symbol_type": "Function",
            "start_line": 50,
            "end_line": 60,
            "code": method1_code,
        },
        # Method 2
        {
            "filepath": "src/PaymentGateway.java",
            "symbol_type": "Function",
            "start_line": 70,
            "end_line": 80,
            "code": method2_code,
        },
    ]
    chunks = pack_file_ast_blocks(blocks, target_tokens=300, tolerance=50)
    assert len(chunks) == 1
    # Check that enclosing class is bound in the metadata header
    assert "Class: public class PaymentGateway implements IPayment" in chunks[0]["text"]
    assert "chargeCreditCard" in chunks[0]["text"]
    assert "refundPayment" in chunks[0]["text"]
