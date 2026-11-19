# 업종 정보가 있는 미국 종목 테이블 탐색
import sys; sys.path.insert(0, "..")
import fcff_analysis_v1 as fa
df = fa.q("""SELECT table_name, column_name FROM information_schema.columns
             WHERE table_schema=DATABASE() AND (column_name LIKE %s OR column_name LIKE %s)""",
          ["%sector%", "%industry%"])
print(df.to_string())
