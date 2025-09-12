# smart_comma_fixer.py
import json

p = "__remote_clean.ipynb"

for i in range(20):  # 최대 20번까지 점진적 수정
    s = open(p, "r", encoding="utf-8").read()
    try:
        json.loads(s)
        print(f"[OK] JSON valid after {i} fixes")
        break
    except json.JSONDecodeError as e:
        pos = e.pos
        # 에러 지점 기준 좌우의 '의미 있는 문자'를 찾음
        l = pos - 1
        while l >= 0 and s[l].isspace():
            l -= 1
        r = pos
        while r < len(s) and s[r].isspace():
            r += 1

        left = s[l] if l >= 0 else ""
        right = s[r] if r < len(s) else ""

        # 일반적으로 } ] " 숫자 뒤에는 값이 끝났다는 뜻 → 그 '다음'에 콤마를 넣는다
        if left and (left in "}]" or left == '"' or left.isdigit()):
            s = s[: l + 1] + "," + s[l + 1 :]
            where = "after-left"
        else:
            # 예외적으로 오른쪽이 { [ " 로 시작하면 그 '앞'에 콤마를 넣는다
            s = s[:r] + "," + s[r:]
            where = "before-right"

        open(p, "w", encoding="utf-8").write(s)
        print(f"[fix {i+1}] inserted comma {where} near pos={pos}, left='{left}', right='{right}'")

else:
    raise SystemExit("[ERR] could not auto-fix after many tries")

# 최종 확인
json.loads(open(p, "r", encoding="utf-8").read())
print("[DONE] file is valid JSON now")
