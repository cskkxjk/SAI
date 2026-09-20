"""Shared rule parsing for recognition and the desktop editor."""

import re
import json


def literal_rule(line):
    """Return a literal pair for editable rules; leave regex rules untouched."""
    if line.startswith("@literal "):
        data = json.loads(line[len("@literal "):])
        source, target = data["from"], data["to"]
        if not isinstance(source, str) or not source or not isinstance(target, str):
            raise ValueError("原词不能为空，原词和替换内容必须是文字")
        return source, target
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    parts = re.split(r"\s+=\s*", line, maxsplit=1)
    if len(parts) == 2:
        source, target = parts[0].strip(), parts[1].strip()
        if source and not any(c in source for c in r"\.^$*+?{}[]|()") and "\\" not in target:
            return source, target
    return None


def encode_literal(source, target):
    if not source:
        raise ValueError("请填写识别出的原词")
    return "@literal " + json.dumps({"from": source, "to": target}, ensure_ascii=False)


def parse_rules(text, strict=True):
    rules = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = re.split(r"\s+=\s*", line, maxsplit=1)
        try:
            if line.startswith("@literal "):
                source, target = literal_rule(line)
                rules[re.escape(source)] = (
                    re.compile(re.escape(source)), target.replace("\\", "\\\\"))
                continue
            if len(parts) != 2 or not parts[0].strip():
                raise ValueError("格式应为 查找内容 = 替换内容")
            pattern = parts[0].strip()
            replacement = parts[1].strip().replace(r"\s", " ")
            compiled = re.compile(pattern)
            compiled.sub(replacement, "")
            rules[pattern] = (compiled, replacement)
        except (ValueError, re.error, KeyError, TypeError) as exc:
            if strict:
                raise ValueError(f"第 {number} 行：{exc}") from exc
    return list(rules.values())
