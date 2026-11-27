#!/usr/bin/env bash
# report.md -> report.pdf  (pandoc -> HTML -> headless Chrome). Run from this folder.
set -euo pipefail
cd "$(dirname "$0")"
pandoc report.md -s --embed-resources --css report.css --metadata pagetitle="HR Policy Navigator" -o report.html
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$PWD/report.pdf" "file://$PWD/report.html" >/dev/null 2>&1
echo "pages: $(python3 -c "import re;print(len(re.findall(rb'/Type\s*/Page[^s]', open('report.pdf','rb').read())))")"
