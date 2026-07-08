"""Self-host ALL needed Google Fonts subsets (cyrillic + latin) as woff2 and
emit a manifest (fonts/faces.json) that templates.py turns into @font-face rules
with absolute file:// URIs. Verifies Extra Condensed 800 cyrillic coverage.
"""
import hashlib
import json
import re
import urllib.request
from pathlib import Path

OUT = Path("/home/office/multi2/apps/carousel-studio/server/templates_html/fonts")
OUT.mkdir(parents=True, exist_ok=True)
for old in OUT.glob("*.woff2"):
    old.unlink()

CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
CSS_URL = ("https://fonts.googleapis.com/css2?"
           "family=Fira+Sans+Condensed:wght@600;700;800&"
           "family=Fira+Sans+Extra+Condensed:wght@800&"
           "family=PT+Sans:wght@400;700&display=swap&subset=cyrillic,latin")

# Only self-host these subsets (Russian + latin/digits). Identify by unicode-range.
WANT = {"cyrillic": "U+0301", "latin": "U+00ff"}  # marker substrings


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": CHROME_UA})
    return urllib.request.urlopen(req, timeout=30).read()


css = fetch(CSS_URL).decode()
blocks = re.findall(r"/\*[^*]*\*/\s*@font-face\s*\{[^}]*\}", css)
if not blocks:
    blocks = re.findall(r"@font-face\s*\{[^}]*\}", css)

faces = []
seen_cyr = set()
for b in blocks:
    fam = re.search(r"font-family:\s*'([^']+)'", b).group(1)
    wght = (re.search(r"font-weight:\s*(\d+)", b) or ["", "400"])[1]
    urlm = re.search(r"url\((https://[^)]+\.woff2)\)", b)
    urange = re.search(r"unicode-range:\s*([^;]+);", b)
    if not urlm or not urange:
        continue
    ur = urange.group(1).strip()
    # classify subset
    subset = None
    if "U+0400" in ur or "U+0301" in ur:  # cyrillic / cyrillic-ext
        subset = "cyrillic"
    elif "U+0000" in ur or "U+00" in ur:  # latin / latin-ext
        subset = "latin"
    if subset is None:
        continue
    data = fetch(urlm.group(1))
    h = hashlib.md5(urlm.group(1).encode()).hexdigest()[:6]
    name = f"{fam.lower().replace(' ', '-')}-{wght}-{subset}-{h}.woff2"
    (OUT / name).write_bytes(data)
    faces.append({"family": fam, "weight": int(wght), "unicode_range": ur, "file": name})
    if subset == "cyrillic":
        seen_cyr.add((fam, int(wght)))

(OUT / "faces.json").write_text(json.dumps(faces, ensure_ascii=False, indent=2))

print(f"self-hosted {len(faces)} font faces:")
for f in faces:
    print(f"  {f['family']:30} w{f['weight']}  {f['file']}")
print("\ncyrillic-covered faces:", sorted(seen_cyr))
print(">>> Fira Sans Extra Condensed 800 cyrillic:",
      ("Fira Sans Extra Condensed", 800) in seen_cyr)
