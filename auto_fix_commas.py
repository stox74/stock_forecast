# auto_fix_commas.py
import re, json

p="__remote_clean.ipynb"  # 충돌 제거 결과물에 적용
s=open(p,"r",encoding="utf-8").read()

# 1) 객체 사이(}\n{) 누락 콤마 보강
s = re.sub(r"\}\s*\{", "},\n{", s)
# 2) 배열 닫힘 뒤 객체 시작(]\n{) 보강
s = re.sub(r"\]\s*\{", "],\n{", s)
# 3) ], } 앞에 잘못 붙은 끝 콤마 제거
s = re.sub(r",\s*([\]\}])", r"\1", s)

open(p,"w",encoding="utf-8").write(s)

# 유효성 체크 (에러면 위치를 알려줌)
try:
    json.load(open(p,"r",encoding="utf-8"))
    print("[OK] JSON valid after comma fix")
except json.JSONDecodeError as e:
    print("[ERR]", e)