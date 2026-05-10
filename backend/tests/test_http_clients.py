import ast
from pathlib import Path


def test_runtime_httpx_clients_ignore_container_proxy_environment() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    missing: list[str] = []

    for path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "AsyncClient"
                and isinstance(func.value, ast.Name)
                and func.value.id == "httpx"
            ):
                continue
            trust_env = next((kw.value for kw in node.keywords if kw.arg == "trust_env"), None)
            if not (isinstance(trust_env, ast.Constant) and trust_env.value is False):
                missing.append(f"{path.relative_to(app_root.parent)}:{node.lineno}")

    assert missing == []


def test_runtime_websocket_clients_ignore_container_proxy_environment() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    missing: list[str] = []

    for path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "connect"
                and isinstance(func.value, ast.Name)
                and func.value.id == "websockets"
            ):
                continue
            proxy = next((kw.value for kw in node.keywords if kw.arg == "proxy"), None)
            if not (isinstance(proxy, ast.Constant) and proxy.value is None):
                missing.append(f"{path.relative_to(app_root.parent)}:{node.lineno}")

    assert missing == []
