
import urllib.request
import urllib.error
import json
import os

from tree_sitter import Language, Parser, Query, QueryCursor
import tree_sitter_python
import tree_sitter_javascript
import tree_sitter_typescript
import tree_sitter_java

def fetch_repo_tree(github_repo: str, github_token: str = None) -> list:
   
    # Use recursive=1 to get all files in all nested folders in one single API call
    url = f"https://api.github.com/repos/{github_repo}/git/trees/main?recursive=1"
    req = urllib.request.Request(url, headers={"User-Agent": "TasKode-App"})
    
    if github_token:
        req.add_header('Authorization', f'Bearer {github_token}')

    try:
        with urllib.request.urlopen(req) as response:
            tree_data = json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Failed to fetch repo tree: HTTP {e.code} for {github_repo}")
        return []
    except Exception as e:
        print(f"An error occurred: {e}")
        return []

    # Only keep production code files (blobs) that end with our target extensions
    valid_extensions = ('.py', '.js', '.jsx', '.ts', '.tsx', '.java')
    ignored_patterns = (
        'test/', 'tests/', '__tests__/', 'spec/', 'specs/',
        '/test', '/tests', 'test_', '_test.', '.test.', '.spec.', 'test.java', 'tests.java',
        'node_modules/', 'dist/', 'build/', 'target/', 'vendor/', '.venv/', 'venv/',
    )

    def is_valid_source_file(path: str) -> bool:
        if not path.endswith(valid_extensions):
            return False
        lower_path = "/" + path.lower().lstrip("/")
        for ignored in ignored_patterns:
            if ignored in lower_path:
                return False
        return True
    
    code_files = [
        item['path'] for item in tree_data.get('tree', []) 
        if item['type'] == 'blob' and is_valid_source_file(item['path'])
    ]
    
    return code_files


def fetch_file_content(github_repo: str, filepath: str, github_token: str = None) -> str:
    # Attempt to fetch from the 'main' branch first
    raw_url = f"https://raw.githubusercontent.com/{github_repo}/main/{filepath}"
    
    req = urllib.request.Request(raw_url, headers={"User-Agent": "TasKode-App"})
    if github_token:
        req.add_header('Authorization', f'Bearer {github_token}')
        
    try:
        with urllib.request.urlopen(req) as response:
            return response.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        # Fallback: Many older repositories still use 'master' instead of 'main'
        if e.code == 404:
            master_url = f"https://raw.githubusercontent.com/{github_repo}/master/{filepath}"
            req_master = urllib.request.Request(master_url, headers={"User-Agent": "TasKode-App"})
            if github_token:
                req_master.add_header('Authorization', f'Bearer {github_token}')
            try:
                with urllib.request.urlopen(req_master) as response:
                    return response.read().decode('utf-8')
            except Exception:
                pass
        
        print(f"Failed to fetch {filepath}: HTTP {e.code}")
        return ""
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return ""


def extract_symbols_multilang(code: str, file_extension: str) -> list:
    
    # Map file extensions to their Tree-sitter language modules
    lang_map = {
        ".py": ("python", tree_sitter_python.language()),
        ".js": ("javascript", tree_sitter_javascript.language()),
        ".jsx": ("javascript", tree_sitter_javascript.language()),
        ".ts": ("typescript", tree_sitter_typescript.language_typescript()),
        ".tsx": ("tsx", tree_sitter_typescript.language_tsx()),
        ".java": ("java", tree_sitter_java.language()),
    }
    
    if file_extension not in lang_map:
        return []

    lang_name, lang_ptr = lang_map[file_extension]

    try:
        language = Language(lang_ptr)
        parser = Parser(language)
        
        code_bytes = bytes(code, "utf8")
        tree = parser.parse(code_bytes)
        
        if lang_name == "python":
            query_str = """
            (class_definition name: (identifier) @class)
            (function_definition name: (identifier) @function)
            """
        elif lang_name == "java":
            query_str = """
            (class_declaration name: (identifier) @class)
            (interface_declaration name: (identifier) @class)
            (record_declaration name: (identifier) @class)
            (method_declaration name: (identifier) @function)
            (constructor_declaration name: (identifier) @function)
            """
        else:
            query_str = """
            (class_declaration name: (identifier) @class)
            (function_declaration name: (identifier) @function)
            (method_definition name: (property_identifier) @function)
            (variable_declarator name: (identifier) @function value: (arrow_function))
            """
            
        # 1. Create the Query
        query = Query(language, query_str)
        # 2. Wrap it in a QueryCursor
        query_cursor = QueryCursor(query)
        # 3. Call captures() on the cursor
        captures = query_cursor.captures(tree.root_node)
        
    except Exception as e:
        print(f"Parsing error: {e}")
        return []

    symbols = []
    
    # captures is now guaranteed to be a dictionary: {'class': [node1], 'function': [node2]}
    if isinstance(captures, dict):
        for tag, nodes in captures.items():
            for node in nodes:
                symbol_name = code_bytes[node.start_byte:node.end_byte].decode('utf8')
                symbols.append(f"  class {symbol_name}" if tag == 'class' else f"  function {symbol_name}()")

    return symbols

   
def sync_project_ast(project, github_token: str = None) -> str:
    
    code_files = fetch_repo_tree(project.github_repo, github_token)
    if not code_files:
        return "Failed to fetch files or repository is empty."

    outline_lines = []

    # Cap at 30 files to prevent API rate limits and keep the LLM prompt reasonably sized
    for filepath in code_files[:30]:
        code = fetch_file_content(project.github_repo, filepath, github_token)
        if not code:
            continue
            
        _, ext = os.path.splitext(filepath)
        symbols = extract_symbols_multilang(code, ext)

        if symbols:
            outline_lines.append(f"File: {filepath}")
            outline_lines.extend(symbols)
            outline_lines.append("") # Blank line separator
            
    if not outline_lines:
        return "No functions or classes found in the repository."

    project.ast_outline = "\n".join(outline_lines)
    project.save()
    
    return "Sync successful"