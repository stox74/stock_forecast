# strip_conflicts_all.py
import re

# __remote_clean.ipynb가 없으면 __remote_utf8.ipynb에서 시작
for src in ["__remote_clean.ipynb", "__remote_utf8.ipynb"]:
    try:
        s = open(src, "r", encoding="utf-8").read()
        break
    except FileNotFoundError:
        pass
else:
    raise SystemExit("No input file found")

# <<<<<<< HEAD ... ======= ... >>>>>>> hash 블록을 반복적으로 제거 (BOTTOM 유지)
pat = re.compile(r"<<<<<<< HEAD.*?=======\r?\n(.*?)\r?\n>>>>>>> [0-9a-f]+\r?\n", re.S)
count = 0
while True:
    s2, n = pat.subn(r"\1\n", s)
    count += n
    if n == 0:
        break
    s = s2

open("__remote_clean.ipynb", "w", encoding="utf-8").write(s)
print(f"[DONE] removed conflict blocks: {count}, wrote __remote_clean.ipynb")