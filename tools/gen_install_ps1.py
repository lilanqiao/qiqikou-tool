"""由 install.ps1.template 生成 install.ps1：
含中文的双引号字符串改写成 (U '\\uXXXX…')，让文件变成纯 ASCII，
这样 `irm | iex` 在 Windows PowerShell 5.1 上也不会出现乱码。"""
import os, re

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "install.ps1.template"), encoding="utf-8").read()


def esc(m):
    body = m.group(1)
    if all(ord(c) < 128 for c in body):
        return m.group(0)
    assert "$" not in body, f"含中文的字符串里不能插变量：{body}"
    return "(U '" + "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in body) + "')"


out = re.sub(r'"([^"\n]*)"', esc, src)
bad = [l for l in out.splitlines() if any(ord(c) >= 128 for c in l)]
assert not bad, "仍有非 ASCII 字符：\n" + "\n".join(bad)
open(os.path.join(HERE, "..", "install.ps1"), "w", encoding="ascii", newline="\r\n").write(out)
print("install.ps1 生成完毕，纯 ASCII")
