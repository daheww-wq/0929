#!/bin/bash
# 사용: tools/render.sh A1 B1-2 ...   (인자 없으면 extract.py가 찾은 바뀐 흐름만)
# 한글 폰트(Noto Sans KR / Noto Sans CJK KR)가 설치되어 있어야 글자가 깨지지 않는다.
# 크롬 경로를 지정하려면 CHROME_PATH=/path/to/chrome tools/render.sh ...
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/flows"
[ -d node_modules ] || npm install --silent
CODES="$*"; [ -z "$CODES" ] && CODES="$(python3 "$ROOT/tools/extract.py")"
[ -z "$CODES" ] && { echo "바뀐 흐름 없음"; exit 0; }
P=$(mktemp --suffix=.json)
if [ -n "$CHROME_PATH" ]; then echo "{\"executablePath\":\"$CHROME_PATH\",\"args\":[\"--no-sandbox\"]}" > $P; else echo '{"args":["--no-sandbox"]}' > $P; fi
for f in $CODES; do
  npx mmdc -p $P -c cfg.json -b transparent -I "flow$f" -i src/$f.mmd -o svg/$f.svg >/dev/null 2>/tmp/mmdc_err.txt \
    && echo "$f ok" || { echo "$f FAIL"; head -3 /tmp/mmdc_err.txt; }
done
rm -f $P
