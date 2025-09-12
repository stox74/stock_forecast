# strip_conflicts_all.py
import os, re

# 입력 소스 선택
for src in ["__remote_clean.ipynb", "__remote_utf8.ipynb", "__remote.ipynb"]:
    if os.path.exists(src):
        with open(src, "r", encoding="utf-8", errors="ignore") as f:
            s = f.read()
        break
else:
    raise SystemExit("No input file found")

# <<<<<<< HEAD ... ======= ... >>>>>>> hash  블록을 반복 제거 (아래쪽을 유지)
pat = re.compile(r"<<<<<<< HEAD(?:.|\r|\n)*?=======\r?\n((?:.|\r|\n)*?)\r?\n>>>>>>> [0-9a-f]+\r?\n", re.S)

count = 0
while True:
    s2, n = pat.subn(r"\1\n", s)
    count += n
    if n == 0:
        break
    s = s2

with open("__remote_clean.ipynb", "w", encoding="utf-8") as f:
    f.write(s)

print(f"[DONE] removed conflict blocks: {count}, wrote __remote_clean.ipynb")