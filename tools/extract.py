"""03 문서 9절의 mermaid 블록을 flows/src/<코드>.mmd로 뽑는다. 바뀐 코드만 출력한다."""
import re, pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
src = (root/'docs/서재앱_03_기능과흐름.md').read_text(encoding='utf-8')
sec = src[src.index('**9. 흐름도**'):]
out = root/'flows/src'; out.mkdir(parents=True, exist_ok=True)
changed = []
for m in re.finditer(r'\n([A-F]\d+(?:-\d)?)\. ([^\n]+)\n\n(?:[^\n`]*\n\n)?```mermaid\n(.*?)```', sec, re.S):
    code, body = m.group(1), m.group(3)
    f = out/f'{code}.mmd'
    if not f.exists() or f.read_text(encoding='utf-8') != body:
        f.write_text(body, encoding='utf-8'); changed.append(code)
print(' '.join(changed))
