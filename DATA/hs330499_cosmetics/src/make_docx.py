# -*- coding: utf-8 -*-
"""한국화장품제조(003350) 매출 예측 방식 — Word 문서 생성"""
from __future__ import annotations
from pathlib import Path
import docx
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

FONT = "맑은 고딕"
NAVY = RGBColor(0x1F, 0x38, 0x64)
BLUE2 = RGBColor(0x2F, 0x54, 0x96)
GRAY = RGBColor(0x59, 0x59, 0x59)
RED = RGBColor(0xA3, 0x2D, 0x2D)
GREEN = RGBColor(0x3B, 0x6D, 0x11)
BROWN = RGBColor(0x83, 0x3C, 0x00)
OLIVE = RGBColor(0x7F, 0x60, 0x00)


def _font(run, size=10, bold=False, color=None, name=FONT):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rf = rPr.find(qn('w:rFonts'))
    if rf is None:
        rf = OxmlElement('w:rFonts'); rPr.append(rf)
    for a in ('w:ascii', 'w:hAnsi', 'w:eastAsia', 'w:cs'):
        rf.set(qn(a), name)
    return run


def para(doc, text="", size=10, bold=False, color=None, align=None,
         space_after=6, space_before=3, indent=None, line=1.45):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_after = Pt(space_after)
    pf.space_before = Pt(space_before)
    pf.line_spacing = line
    if align is not None:
        p.alignment = align
    if indent is not None:
        pf.left_indent = Cm(indent)
    if text:
        _font(p.add_run(text), size, bold, color)
    return p


def rich(doc, parts, size=10, indent=None, space_after=6, line=1.45):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_after = Pt(space_after); pf.space_before = Pt(3); pf.line_spacing = line
    if indent is not None:
        pf.left_indent = Cm(indent)
    for t, b, c in parts:
        _font(p.add_run(t), size, b, c)
    return p


def heading(doc, text, level=1):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16 if level == 1 else 12)
    p.paragraph_format.space_after = Pt(7)
    p.style = doc.styles['Heading %d' % level]
    _font(p.add_run(text), 14 if level == 1 else 11.5, True,
          NAVY if level == 1 else BLUE2)
    return p


def bullet(doc, text, level=0, size=10):
    p = doc.add_paragraph(style='List Bullet' if level == 0 else 'List Bullet 2')
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.line_spacing = 1.4
    _font(p.add_run(text), size)
    return p


def shade(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr()
    el = OxmlElement('w:shd')
    el.set(qn('w:val'), 'clear'); el.set(qn('w:color'), 'auto')
    el.set(qn('w:fill'), hex_fill)
    tcPr.append(el)


def set_cell(cell, text, size=9, bold=False, color=None, align=None, fill=None):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.25
    if align is not None:
        p.alignment = align
    for i, line in enumerate(str(text).split("\n")):
        if i:
            p = cell.add_paragraph()
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(3)
            if align is not None:
                p.alignment = align
        _font(p.add_run(line), size, bold, color)
    if fill:
        shade(cell, fill)


def table(doc, widths_cm, header, rows):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, w in enumerate(widths_cm):
        for r in t.rows:
            r.cells[i].width = Cm(w)
    for i, h in enumerate(header):
        set_cell(t.rows[0].cells[i], h, 9, True, RGBColor(0xFF, 0xFF, 0xFF),
                 WD_ALIGN_PARAGRAPH.CENTER if i else WD_ALIGN_PARAGRAPH.LEFT, '1F3864')
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for i, c in enumerate(row):
            if isinstance(c, dict):
                txt, b, col = c.get('t'), c.get('b', False), c.get('c')
            else:
                txt, b, col = c, False, None
            set_cell(cells[i], txt, 9, b, col,
                     WD_ALIGN_PARAGRAPH.CENTER if i else WD_ALIGN_PARAGRAPH.LEFT,
                     'F2F2F2' if ri % 2 else None)
        for i, w in enumerate(widths_cm):
            cells[i].width = Cm(w)
    para(doc, "", space_after=4, space_before=0)
    return t


def box(doc, lines, fill='FFF2CC', width_cm=16.0):
    t = doc.add_table(rows=1, cols=1)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    c = t.rows[0].cells[0]
    c.width = Cm(width_cm)
    c.text = ""
    shade(c, fill)
    for i, (txt, sz, b, col) in enumerate(lines):
        p = c.paragraphs[0] if i == 0 else c.add_paragraph()
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.line_spacing = 1.4
        _font(p.add_run(txt), sz, b, col)
    para(doc, "", space_after=4, space_before=0)
    return t


# ────────────────────────────────────────── 문서
def build():
    doc = Document()
    s = doc.sections[0]
    s.page_width, s.page_height = Cm(21.0), Cm(29.7)      # A4
    s.left_margin = s.right_margin = Cm(2.5)
    s.top_margin = s.bottom_margin = Cm(2.2)

    st = doc.styles['Normal']
    st.font.name = FONT
    st.font.size = Pt(10)
    st.element.rPr.rFonts.set(qn('w:eastAsia'), FONT)

    # ── 표지 ──
    para(doc, "", space_before=90, space_after=0)
    para(doc, "한국화장품제조 (003350)", 20, True, NAVY, WD_ALIGN_PARAGRAPH.CENTER, 4)
    para(doc, "분기 매출액 · 영업이익 예측 방식", 15, True, NAVY,
         WD_ALIGN_PARAGRAPH.CENTER, 14)
    para(doc, "HS 수출지표를 보조지표로 활용하는 절차와 그 검증 결과",
         11, False, GRAY, WD_ALIGN_PARAGRAPH.CENTER, 30)

    box(doc, [
        ("이 문서의 핵심", 12, True, OLIVE),
        ("수출지표는 「보조 점검지표」이지 「예측 엔진」이 아니다.", 10.5, True, None),
        ("기준모형(과거 실적만 사용) 대비 예측오차 개선폭은 4~7%에 그치며, 통계적 유의성은 "
         "확인되지 않았다(DM 검정 p>0.25). 다만 화장품 계열의 개선폭 평균(+2.4%)이 "
         "무관 품목 위약군 평균(-2.1%)을 일관되게 상회해, 약한 신호는 존재한다고 본다.", 10, False, None),
        ("따라서 수출지표는 기준모형 전망치의 상·하방을 확인하는 용도로만 쓴다.", 10, True, RED),
    ])

    para(doc, "", space_after=16)
    para(doc, "작성일 2026-09-24", 9.5, False, GRAY, WD_ALIGN_PARAGRAPH.CENTER, 2)
    para(doc, "검증 표본: 2016Q4 ~ 2026Q2 (매출 39분기 / 영업이익률 43분기) · 확장창 표본밖 검증",
         9.5, False, GRAY, WD_ALIGN_PARAGRAPH.CENTER, 0)

    doc.add_page_break()

    # ── 1 ──
    heading(doc, "1. 결론 요약", 1)
    para(doc, "한국화장품제조의 분기 실적은 과거 실적만으로 구성한 회귀 기준모형이 예측의 "
              "중심이며, HS 화장품 수출지표는 여기에 소폭의 개선을 더한다. 개선폭과 그 "
              "신뢰도는 아래와 같다.")
    table(doc, [4.2, 3.6, 3.8, 4.4],
          ["구분", "기준모형", "수출지표 추가", "판정"],
          [["매출액 YoY 오차(MAE)", "18.20%p", {"t": "17.15%p", "b": True, "c": GREEN}, "-5.8% 개선"],
           ["매출액 수준 오차", "4.10십억원", {"t": "3.90십억원", "b": True, "c": GREEN}, "-4.9% 개선"],
           ["영업이익률 오차", "4.31%p", "4.24%p", "-1.6% 개선"],
           ["통계적 유의성", "—", {"t": "DM p > 0.25", "b": True, "c": RED}, "유의하지 않음"],
           ["위약(무관품목) 대조", "평균 -2.1%", {"t": "평균 +2.4%", "b": True}, "약한 신호 존재"]])
    para(doc, "요약하면, 수출지표를 넣으면 오차가 줄기는 하지만 그 폭이 작고 통계적으로 "
              "입증되지 않았다. 그러나 화장품과 무관한 품목(반도체·석유제품·승용차)이나 "
              "순수 시간추세를 같은 자리에 넣었을 때는 평균적으로 오차가 오히려 늘었다. "
              "즉 개선은 우연이 아닐 가능성이 있으나, 단독 판단 근거로 쓸 만큼 크지는 않다.")

    # ── 2 ──
    heading(doc, "2. 데이터 원천", 1)
    heading(doc, "2.1 실적 데이터", 2)
    table(doc, [3.0, 4.8, 2.0, 6.2],
          ["항목", "테이블 / 컬럼", "단위", "비고"],
          [["분기 매출액", "korea_fs_data_from_DG\nitem_code = M000904001", "천원", "당분기 개별 수치"],
           ["분기 영업이익", "korea_fs_data_from_DG\nitem_code = M000906001", "천원", "당분기 개별 수치"],
           ["검증용 원자료", "korea_fs_data_from_DART_V3", "원", "thstrm_amount = 당분기"]])
    rich(doc, [("중요: ", True, None),
               ("한국화장품제조는 DART 기준 ", False, None),
               ("fs_div = OFS, 즉 별도(개별) 재무제표", True, None),
               ("다. 연결 기준인 종목과 수준을 직접 비교하면 안 된다. 또한 DataGuide 값은 "
                "이미 「당분기 개별」 수치로 저장되어 있어 누적치 차감 변환이 필요 없다. "
                "DART 원자료와 10개 분기를 대조한 결과 괴리 0.0000%로 일치함을 확인했다.", False, None)])
    para(doc, "사용 가능 기간은 2009Q4 ~ 2026Q2로 67개 분기이며, 기간 내 결측 분기는 없다. "
              "다만 영업이익이 0 이하인 분기가 19개 있어, 영업이익 증가율(YoY) 대신 "
              "영업이익률을 목표변수로 사용한다.")

    heading(doc, "2.2 수출 데이터", 2)
    table(doc, [2.6, 5.2, 1.8, 6.4],
          ["항목", "테이블 (indicator)", "단위", "비고"],
          [["수출총액", "korea_monthly_trade_data (expDlr)", "USD", "전국 기준, 2007-01 ~"],
           ["수출중량", "korea_monthly_trade_data (expWgt)", "kg", "전국 기준, 2007-01 ~"]])
    para(doc, "주력 HS 코드는 330499(기초·색조 화장품 기타)이며, 검증 결과 330420(눈화장용 "
              "제품류)이 더 나은 성과를 보였다. 두 소스(korea_monthly_trade_data / _v2)를 "
              "겹치는 228개월에서 대조한 결과 최대 괴리는 수출액 0.91%, 중량 0.08%였다.")

    doc.add_page_break()

    # ── 3 ──
    heading(doc, "3. 예측 절차", 1)
    para(doc, "아래 5단계를 분기마다 반복한다. 핵심은 「그 시점에 실제로 알 수 있는 정보만 "
              "쓴다」는 것이다.")

    heading(doc, "3.1 공표 시차 — 반드시 지킬 것", 2)
    table(doc, [5.0, 4.0, 7.0],
          ["자료", "공표 시점", "함의"],
          [["HS 6단위 월별 수출", "익월 15일경", "분기 첫 달 수출은 둘째 달 15일에 확인"],
           ["분기보고서(실적)", "분기 종료 후 45일", "분기 시작 시점에는 직전 분기 실적을 모른다"]])
    rich(doc, [("두 번째 항목이 특히 중요하다. ", True, None),
               ("분기 Q가 시작되는 시점에는 Q-1 실적이 아직 공시되지 않았으므로, "
                "자기회귀 항의 시차를 1이 아니라 ", False, None),
               ("2로 두어야", True, None),
               (" 한다. 이를 지키지 않으면 실제로는 불가능한 예측을 만들게 된다.", False, None)])

    heading(doc, "3.2 단계별 절차", 2)
    rich(doc, [("① 실적 시계열 구성", True, NAVY)], size=10.5)
    bullet(doc, "DG에서 분기 매출액·영업이익을 조회하고 분기말로 날짜를 정규화한다.")
    bullet(doc, "매출액 YoY(%) = (당분기 ÷ 전년 동기 − 1) × 100")
    bullet(doc, "영업이익률(%) = 영업이익 ÷ 매출액 × 100")

    rich(doc, [("② 수출 시계열 구성", True, NAVY)], size=10.5)
    bullet(doc, "HS 330499·330420의 월별 수출총액(USD)·수출중량(kg)을 조회한다.")
    bullet(doc, "당분기 누적 k개월(k=1,2,3) 합계를 만들고, 전년 동기의 같은 k개월과 "
                "비교해 YoY를 계산한다.")
    bullet(doc, "반드시 증가율(YoY) 형태로 쓴다. 수준(로그) 변수는 추세 공행 때문에 "
                "허위 신호를 만든다.", 1)

    rich(doc, [("③ 기준모형 추정", True, NAVY)], size=10.5)
    para(doc, "매출액 YoY(t) = a + b₁ × 매출액 YoY(t−1) + b₂ × 매출액 YoY(t−4)",
         10, True, None, None, 3, 3, 0.8)
    para(doc, "영업이익률(t) = a + b₁ × 영업이익률(t−1) + b₂ × 영업이익률(t−4)",
         10, True, None, None, 3, 0, 0.8)
    para(doc, "※ 분기 시작 시점(사전예측)에는 (t−1) 대신 (t−2)를 쓴다.",
         9.5, False, GRAY, None, 6, 2, 0.8)

    rich(doc, [("④ 수출지표 1개 추가", True, NAVY)], size=10.5)
    para(doc, "위 식에 수출변수를 딱 하나만 더한다. 표본이 짧아 두 개 이상 넣으면 "
              "과적합된다.", 10, False, None, None, 6, 2, 0.8)

    rich(doc, [("⑤ 수준 환산", True, NAVY)], size=10.5)
    bullet(doc, "매출액 = 전년 동기 매출액 × (1 + 예측 YoY ÷ 100)")
    bullet(doc, "영업이익 = 예측 매출액 × 예측 영업이익률 ÷ 100")

    doc.add_page_break()

    # ── 4 ──
    heading(doc, "4. 어떤 지표를 언제 보는가", 1)
    para(doc, "분기 진행에 따라 이용 가능한 정보가 달라진다. 시점별 권장 지표는 다음과 같다.")
    table(doc, [2.8, 2.0, 5.4, 2.6, 2.2],
          ["시점", "분기 경과", "권장 수출지표", "오차(MAE)", "개선율"],
          [["분기 시작", "0일", "직전분기 앞 2개월 수출총액 YoY", "21.36%p", "-2.1%"],
           ["둘째 달 중순", "45일", "당분기 1개월 수출중량 YoY", "17.54%p", "-3.6%"],
           [{"t": "셋째 달 중순", "b": True}, {"t": "75일", "b": True},
            {"t": "당분기 2개월 수출중량 YoY", "b": True},
            {"t": "17.15%p", "b": True, "c": GREEN}, {"t": "-5.8%", "b": True, "c": GREEN}],
           ["분기 종료 +15일", "105일", "당분기 3개월 수출중량 YoY", "17.47%p", "-4.0%"]])
    box(doc, [
        ("가장 유용한 시점은 분기 75일째다.", 11.5, True, None),
        ("분기가 끝나기 전이고, 실적 발표(분기 종료 후 45일)보다 두 달 이상 앞선다. "
         "이 시점에 당분기 2개월치 수출이 공표되어 있으며, 오차 개선폭도 가장 크다(-5.8%).",
         10, False, None),
        ("주의: 3개월 전부가 공표된 뒤가 오히려 나빠진다는 점은, 이 개선이 정보량 증가보다 "
         "표본 잡음에 가깝다는 신호이기도 하다.", 10, False, RED),
    ], fill='E2EFDA')

    heading(doc, "4.1 HS 코드별 성과", 2)
    table(doc, [2.8, 5.2, 3.0, 2.4, 1.6],
          ["HS 코드", "품목", "지표", "오차", "개선율"],
          [[{"t": "330420", "b": True}, {"t": "눈화장용 제품류", "b": True}, "수출총액 YoY",
            {"t": "16.94%p", "b": True, "c": GREEN}, {"t": "-6.9%", "b": True, "c": GREEN}],
           ["3304999000", "330499 세분류", "수출총액 YoY", "17.60%p", "-4.5%"],
           ["330499", "기초·색조 화장품 기타", "수출중량 YoY", "17.47%p", "-4.0%"],
           ["합성(3304)", "330410+330420+330499", "수출중량 YoY", "17.45%p", "-4.1%"],
           ["—", "기준모형(수출 미사용)", "—", "18.20%p", "0.0%"]])
    para(doc, "330499 단독보다 330420(눈화장용)이 더 나았다. 다만 15개 HS × 3개 지표를 "
              "비교해 고른 최상위 값이므로 선택편의가 포함되어 있다. 실제 운용에서는 "
              "330420과 330499를 함께 보고 방향이 일치하는지 확인하는 편이 안전하다.")

    doc.add_page_break()

    # ── 5 ──
    heading(doc, "5. 검증 방법과 결과", 1)
    heading(doc, "5.1 검증 설계", 2)
    bullet(doc, "확장창(expanding window) rolling-origin 방식. 원점 t에서 [0, t) 자료만으로 "
                "계수를 추정해 t를 예측하고, 원점을 한 분기씩 전진시킨다.")
    bullet(doc, "변수 선택도 훈련표본 안에서만 수행한다(BIC 최소). 미래 자료는 선택에도 "
                "학습에도 쓰지 않는다.")
    bullet(doc, "모든 모형을 동일한 평가분기 위에서 비교한다.")
    bullet(doc, "최소 훈련표본 20분기. 평가 구간은 매출 39분기(2016Q4~2026Q2), "
                "영업이익률 43분기.")
    bullet(doc, "누수(look-ahead) 자동 점검 18개 항목 전부 통과.")

    heading(doc, "5.2 위약(placebo) 검정 — 가장 중요한 절차", 2)
    para(doc, "한국화장품제조 매출과 논리적으로 무관한 계열(반도체·석유제품·승용차 수출, "
              "순수 시간추세)을 수출지표 자리에 똑같이 넣어 비교했다. 위약에서도 같은 "
              "개선이 나온다면 그 개선은 정보가 아니라 잡음이다.")
    table(doc, [5.4, 3.2, 3.2, 4.2],
          ["구분", "평균 개선율", "최대 개선율", "해석"],
          [[{"t": "화장품 계열(330499·330420)", "b": True},
            {"t": "+2.43%", "b": True, "c": GREEN}, "+6.94%", "신호 있음"],
           ["위약(반도체·석유·승용차)", {"t": "-2.08%", "c": RED}, "+4.91%", "평균적으로 악화"]])
    rich(doc, [("해석: ", True, None),
               ("화장품 계열의 평균 개선율이 위약 평균을 확실히 상회하므로 약한 신호는 "
                "존재한다. 그러나 ", False, None),
               ("위약 중 최선(+4.91%)이 화장품 최선(+6.94%)에 육박", True, None),
               ("하므로, 개별 조합 하나를 떼어내 「유의하다」고 주장할 수는 없다.", False, None)])

    heading(doc, "5.3 유의성 검정", 2)
    para(doc, "Diebold-Mariano 검정 결과 모든 조합에서 p > 0.25로, 기준모형과의 차이가 "
              "통계적으로 유의하지 않았다. 예측결합(forecast encompassing) 회귀에서도 "
              "수출지표 예측이 추가 정보를 갖는다는 증거는 확인되지 않았다(p = 0.55~0.85).")

    doc.add_page_break()

    # ── 6 ──
    heading(doc, "6. 한계와 주의사항", 1)
    box(doc, [
        ("반드시 인과관계로 해석하지 말 것", 11.5, True, BROWN),
        ("HS 수출지표는 산업 전체 통계이며, 한국화장품제조 한 기업의 수출이 아니다. "
         "상관이 있어도 「산업 수요 → 개별기업 매출」이라는 인과의 증거가 아니다.", 10, False, None),
    ], fill='FBE4D5')
    table(doc, [3.6, 12.4],
          ["한계", "내용"],
          [["ODM 사업구조", "고객사(브랜드사)의 수출이 자사 매출로 인식되는 시점이 "
                          "통관 시점과 다르다."],
           ["내수/수출 미분리", "내수·수출 매출을 분리한 자료가 없어 전체 매출을 대상으로 했다."],
           ["수정치 사용", "DB에 공표시점별 원본(vintage)이 없어 최신 수정치를 썼다. "
                        "2025년 구간 수출액 수정폭이 최대 0.91% 확인됐다. 실제 실시간 "
                        "성능은 본 결과보다 약간 나쁠 수 있다."],
           ["다중비교", "HS 15종 × 지표 3종 × 시차를 비교했으므로 최상위 성과는 "
                     "선택편의로 과대평가되어 있다."],
           ["분류 재배분", "330499는 「기타」 세번이므로 상위 3304 내 품목 재배분 "
                       "가능성을 배제할 수 없다."],
           ["속보치 부재", "관세청 10일 속보 테이블에 화장품 항목이 없어, 분기 진행 중 "
                       "더 이른 시점의 정보를 쓸 수 없다."]])

    heading(doc, "6.1 다음 분석에 필요한 자료", 2)
    bullet(doc, "기업별 수출실적(관세청 기업별 수출입실적 또는 DART 주석) — 산업 전체 "
                "대신 해당 기업 수출을 쓰면 관계가 훨씬 직접적이다.")
    bullet(doc, "HS330499 국가별 수출 — 현재 region 컬럼에 「전국」만 적재되어 있다.")
    bullet(doc, "매출의 내수/수출/지역별 분해(DART 사업보고서 주석).")
    bullet(doc, "수출통계 공표시점 vintage 아카이브 — 진정한 실시간 백테스트를 위해 필요.")
    bullet(doc, "화장품 소매판매액지수, 면세점 매출, 환율(USD/KRW).")

    # ── 7 ──
    heading(doc, "7. 운용 지침", 1)
    rich(doc, [("해야 할 것", True, GREEN)], size=11.5)
    bullet(doc, "기준모형(과거 실적 회귀)을 예측의 중심으로 삼는다.")
    bullet(doc, "분기 75일째에 당분기 2개월 수출중량 YoY와 330420 수출총액 YoY를 확인한다.")
    bullet(doc, "두 지표의 방향이 기준모형 전망과 어긋나면 전망의 불확실성이 크다고 판단한다.")
    bullet(doc, "영업이익은 매출 예측에 영업이익률 예측을 곱해 산출한다.")

    rich(doc, [("하지 말아야 할 것", True, RED)], size=11.5)
    bullet(doc, "수출지표만으로 매출을 예측하지 않는다. 단독 설명력은 낮다.")
    bullet(doc, "수준(로그) 변수를 쓰지 않는다. 추세 공행으로 허위 유의성을 만든다.")
    bullet(doc, "설명변수를 두 개 이상 넣지 않는다. 표본이 짧아 과적합된다.")
    bullet(doc, "개선폭 4~7%를 「예측이 된다」고 해석하지 않는다. 매출 증가율 오차는 "
                "여전히 17%p다.")

    box(doc, [
        ("새 지표를 시험할 때마다 위약검정을 함께 돌릴 것", 11.5, True, None),
        ("무관 품목과 순수 시간추세를 같은 자리에 넣어보는 절차다. 이 절차가 없었다면 "
         "본 분석에서도 잘못된 결론에 도달했을 것이다(실제로 에이피알 분석에서 "
         "그런 사례가 발생했다).", 10, False, None),
    ])

    # ── 8 ──
    heading(doc, "8. 재현 방법", 1)
    para(doc, "DATA/hs330499_cosmetics/ 아래에 전체 코드와 산출물이 있다.")
    table(doc, [4.6, 11.4],
          ["파일", "역할"],
          [["src/extract.py", "DB 스키마 확인 · 데이터 추출 · 품질 점검"],
           ["src/features.py", "시계열 변수 생성 · 정보집합 구성"],
           ["src/analysis.py", "시차 상관 · 그래프"],
           ["src/backtest.py", "확장창 표본밖 검증"],
           ["src/deepdive.py", "월별 정보누적 · 대안 HS 탐색"],
           ["src/placebo.py", "위약 검정"],
           ["src/validate.py", "누수 자동 점검 18항목"],
           ["src/run_all.py", "전체 파이프라인 실행"]])
    para(doc, "실행:  cd src && python run_all.py", 10.5, True, None, None, 10, 4, 0.8)
    para(doc, "본 문서의 모든 수치는 위 코드의 실행 결과이며, output/tables/ 의 CSV에서 "
              "확인할 수 있다.", 9, False, GRAY)

    out = Path(__file__).resolve().parents[1] / "output" / "report" / \
        "한국화장품제조_매출예측_방식.docx"
    doc.save(out)
    print("저장 완료:", out)
    print("크기:", "{:,} bytes".format(out.stat().st_size))
    return out


if __name__ == "__main__":
    build()
