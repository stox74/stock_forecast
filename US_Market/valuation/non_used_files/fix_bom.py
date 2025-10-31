# fix_bom.py
import io

with open("__remote.ipynb", "rb") as f:
    raw = f.read()

# BOM 제거하면서 디코딩
text = raw.decode("utf-8-sig")

with open("__remote_clean.ipynb", "w", encoding="utf-8") as f:
    f.write(text)

print("BOM 제거 완료: __remote_clean.ipynb 생성됨")