import pathlib, sys

src = pathlib.Path("__remote.ipynb")
dst = pathlib.Path("__remote_utf8.ipynb")

raw = src.read_bytes()

# 여러 인코딩 시도 (UTF-16 우선)
text = None
for enc in ("utf-16", "utf-16-le", "utf-16-be", "utf-8-sig", "utf-8"):
    try:
        text = raw.decode(enc)
        print(f"[OK] decoded as {enc}")
        break
    except Exception:
        pass

if text is None:
    print("[ERR] failed to decode", file=sys.stderr)
    sys.exit(1)

# UTF-8(BOM 없음)으로 저장
dst.write_text(text, encoding="utf-8")
print(f"[DONE] wrote: {dst}")
