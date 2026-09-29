import re, html, pathlib
root=pathlib.Path(__file__).resolve().parent.parent
src=open(root/'docs/서재앱_03_기능과흐름.md',encoding='utf-8').read()
sec=src[src.index('**9. 흐름도**'):]
# groups
groups=[("A","시작과 계정"),("B","담기"),("C","정리"),("D","읽기"),("E","설정"),("F","공통 예외")]
parts=re.split(r'\n\*\*([A-F])\. ([^*]+)\*\*\n',sec)
content={}
for i in range(1,len(parts),3):
    content[parts[i]]=parts[i+2]
def svg(code):
    t=open(root/f'flows/svg/{code}.svg',encoding='utf-8').read()
    t=re.sub(r'^<\?xml[^>]*>\s*','',t)
    m=re.search(r'max-width:\s*([\d.]+)px',t)
    if m: t=t.replace('width="100%"',f'width="{float(m.group(1)):.0f}"',1)
    return t
def render_group(body):
    out=[]
    # flows: title line then ```mermaid block
    for m in re.finditer(r'\n([A-F]\d+(?:-\d)?)\. ([^\n]+)\n\n(?:[^\n`]*\n\n)?```mermaid\n(.*?)```',body,re.S):
        code,title,merm=m.group(1),m.group(2),m.group(3)
        t,_,screens=title.partition(' · ')
        chips=''.join(f'<span class="chip">{html.escape(x.strip())}</span>' for x in re.split(r'→|/',screens) if x.strip())
        out.append(f'''<article class="flow" id="{code}"><header><span class="code">{code}</span><h3>{html.escape(t)}</h3></header>
<div class="screens">{chips}</div><div class="diagram"><div class="sheet">{svg(code)}</div></div></article>''')
    notes=[]
    for head in ["결정 필요","새로 정한 것"]:
        m=re.search(r'\n[A-F] 묶음에서 (?:나온 )?'+head+r'\n((?:- [^\n]+\n?)+)',body)
        if m:
            items=''.join(f'<li>{html.escape(l[2:])}</li>' for l in m.group(1).strip().split('\n'))
            cls='todo' if head=="결정 필요" else 'done'
            notes.append(f'<div class="note {cls}"><h4>{head}</h4><ul>{items}</ul></div>')
    return '\n'.join(out)+('<div class="notes">'+''.join(notes)+'</div>' if notes else '')
nav=''.join(f'<a href="#g{g}" class="{"on" if g in content else "off"}"><b>{g}</b>{n}</a>' for g,n in groups)
body=''
for g,n in groups:
    if g in content:
        body+=f'<section class="group" id="g{g}"><h2><span>{g}</span>{n}</h2>{render_group(content[g])}</section>'
    else:
        body+=f'<section class="group pending" id="g{g}"><h2><span>{g}</span>{n}</h2><p class="wait">다음 차례에 그려요.</p></section>'
page=open(root/'tools/tpl.html',encoding='utf-8').read().replace('{{NAV}}',nav).replace('{{BODY}}',body)
open(root/'flows/flows.html','w',encoding='utf-8').write(page); print('built',len(page))
