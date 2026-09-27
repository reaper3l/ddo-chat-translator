"""静态检查：找出"用了但没定义/没导入"的名字。

旧版程序就是因为 `messagebox`、`filedialog`、`Toplevel`、`ttk` 这几个名字
没导入，导致"保存记录/关于/纠错/词典"这些按钮一点就报 NameError。
这个脚本用 AST 扫一遍所有源文件，把同类问题在运行前就找出来。

用法：python tools/check_names.py
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".git", "build", "dist", "data"}
ALLOWED = {"__file__", "__name__", "__doc__", "__package__", "__spec__",
           "__builtins__", "__loader__", "__debug__"}


def bound_names(tree: ast.AST) -> set:
    """收集模块里所有被绑定过的名字（尽可能宽松，只报真正没影的名字）。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
    return names


def check_file(path: Path) -> list:
    source = path.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [("语法错误", "第 %s 行：%s" % (exc.lineno, exc.msg))]

    bound = bound_names(tree)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = node.id
            if name in bound or name in ALLOWED or hasattr(builtins, name):
                continue
            problems.append((name, "第 %d 行使用了未定义/未导入的名字" % node.lineno))
    problems.extend(check_shadowed_methods(tree))
    problems.extend(check_missing_self_methods(tree))
    problems.extend(check_self_call_arguments(tree))
    return problems


def check_shadowed_methods(tree: ast.AST) -> list:
    """找出"实例属性覆盖同名方法"的写法。

    例如 RegionPicker 里既定义了 start() 方法、又在 __init__ 里写了
    self.start = None，那么 picker.start() 就会报 'NoneType' object is not callable。
    """
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {child.name for child in node.body
                   if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assigned = set()
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store)
                    and isinstance(sub.value, ast.Name) and sub.value.id == "self"):
                assigned.add(sub.attr)
        for name in sorted(methods & assigned):
            problems.append((name, "类 %s：self.%s = ... 覆盖了同名方法（会导致 not callable）"
                             % (node.name, name)))
    return problems


def check_missing_self_methods(tree: ast.AST) -> list:
    """找出 self.xxx() 里 xxx 根本没定义的情况（例如方法名拼错）。"""
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        defined = {child.name for child in node.body
                   if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assigned = set()
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store)
                    and isinstance(sub.value, ast.Name) and sub.value.id == "self"):
                assigned.add(sub.attr)
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "self"):
                name = sub.func.attr
                if name not in defined and name not in assigned:
                    problems.append((name, "类 %s：调用了未定义的 self.%s()（第 %d 行）"
                                     % (node.name, name, sub.lineno)))
    return problems


def check_self_call_arguments(tree: ast.AST) -> list:
    """检查同类里 self.xxx(...) 传参是否对得上（参数重名、多传、名字写错）。"""
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {child.name: child for child in node.body
                   if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "self"):
                continue
            function = methods.get(call.func.attr)
            if function is None or function.args.vararg or function.args.kwarg:
                continue
            params = [arg.arg for arg in function.args.args]
            decorators = {getattr(d, "id", None) for d in function.decorator_list}
            if "staticmethod" not in decorators:
                params = params[1:]          # 普通方法/类方法：去掉 self / cls
            position_count = len(call.args)
            if position_count > len(params):
                problems.append((call.func.attr,
                                 "类 %s：self.%s() 位置参数太多（第 %d 行）"
                                 % (node.name, call.func.attr, call.lineno)))
                continue
            bound = set(params[:position_count])
            for keyword in call.keywords:
                if keyword.arg is None:
                    continue
                if keyword.arg in bound:
                    problems.append((keyword.arg,
                                     "类 %s：self.%s() 的 %s 既按位置又按关键字传了（第 %d 行）"
                                     % (node.name, call.func.attr, keyword.arg, call.lineno)))
                elif keyword.arg not in params:
                    problems.append((keyword.arg,
                                     "类 %s：self.%s() 没有参数名 %s（第 %d 行）"
                                     % (node.name, call.func.attr, keyword.arg, call.lineno)))
    return problems


def main() -> int:
    targets = [ROOT / "main.py"]
    for folder in ("app", "tools", "tests"):
        targets.extend(sorted((ROOT / folder).rglob("*.py")))

    total_files = 0
    total_problems = 0
    for path in targets:
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        total_files += 1
        problems = check_file(path)
        if problems:
            total_problems += len(problems)
            print("\n%s" % path.relative_to(ROOT))
            for name, message in problems:
                print("  [X] %s —— %s" % (name, message))

    print("\n" + "=" * 56)
    if total_problems:
        print("检查了 %d 个文件，发现 %d 个未定义名字" % (total_files, total_problems))
    else:
        print("检查了 %d 个文件，没有发现未定义名字" % total_files)
    print("=" * 56)
    return 1 if total_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
