"""OM の表の name（ソース上の修飾名）の書き方。

Domain 層。name は「スキーマ.表」のように部品を "." でつないだもの。部品に "." や '"' が入る、または空のときだけ、
その部品を SQL 標準の引用符で囲む（"a.b" → '"a.b"'、'q"t' → '"q""t"'）。普通の名前（sales.orders）はそのまま。
これで name から部品を曖昧さなく取り出せる。
主張の locator は常に引用する別の書き方（mandara.extractors.catalog.quote）。用途が違う（読みやすさと、中身によらない安定）。
"""

_NEEDS_QUOTE = frozenset('."')


def qualify(*parts: str) -> str:
    return ".".join(_quote(p) for p in parts)


def _quote(part: str) -> str:
    if part and not (_NEEDS_QUOTE & set(part)):
        return part
    return '"' + part.replace('"', '""') + '"'


def split_qualified(name: str) -> list[str]:
    """qualify の逆。不正な書き方は ValueError。"""
    parts: list[str] = []
    i = 0
    while True:
        if name.startswith('"', i):
            buf: list[str] = []
            i += 1
            while True:
                end = name.find('"', i)
                if end < 0:
                    raise ValueError(f"引用符が閉じていない: {name!r}")
                buf.append(name[i:end])
                if name.startswith('"', end + 1):
                    buf.append('"')
                    i = end + 2
                    continue
                i = end + 1
                break
            parts.append("".join(buf))
        else:
            end = name.find(".", i)
            part = name[i:] if end < 0 else name[i:end]
            if not part or '"' in part:
                raise ValueError(f"修飾名として不正: {name!r}")
            parts.append(part)
            i = len(name) if end < 0 else end
        if i == len(name):
            break
        if name[i] != ".":
            raise ValueError(f"修飾名として不正: {name!r}")
        i += 1
    if qualify(*parts) != name:
        raise ValueError(f"正規の書き方ではない: {name!r}")
    return parts
