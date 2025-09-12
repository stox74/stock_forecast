
# fix_conflicts_keep_bottom.py
import re

src="__remote_utf8.ipynb"      # 입력
dst="__remote_clean.ipynb"     # 출력

with open(src,"r",encoding="utf-8") as f:
    s=f.read()

# <<<<<<< HEAD ... ======= ... >>>>>>> 해시  블록 제거 (======= 아래쪽을 채택)
pat=re.compile(r"<<<<<<< HEAD\r?\n(.*?)\r?\n=======\r?\n(.*?)\r?\n>>>>>>> [0-9a-f]+\r?\n", re.S)

changed=0
while True:
    s2,n=pat.subn(lambda m: m.group(2)+"\n", s)
    changed+=n
    if n==0: break
    s=s2

with open(dst,"w",encoding="utf-8") as f:
    f.write(s)

print(f"[DONE] removed conflict markers. blocks={changed}. wrote {dst}")