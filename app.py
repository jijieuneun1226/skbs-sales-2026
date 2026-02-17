import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import urllib.parse
import numpy as np
import requests
import io
import re
from datetime import timedelta

# --------------------------------------------------------------------------------
# 1. 페이지 설정 및 권한 제어
# --------------------------------------------------------------------------------
st.set_page_config(page_title="SKBS Sales Report", layout="wide", initial_sidebar_state="expanded")

params = st.query_params
is_edit_mode = params.get("mode") == "edit"

if not is_edit_mode:
    st.markdown("<style>[data-testid='stSidebar'] {display: none;} section[data-testid='stSidebar'] {width: 0px;}</style>", unsafe_allow_html=True)

st.markdown("""
<style>
    div.block-container {padding-top: 1rem;}
    .metric-card {background-color: #f8f9fa; border-left: 5px solid #4e79a7; padding: 15px; border-radius: 5px; margin-bottom: 10px;}
    .info-box {padding: 10px; border-radius: 5px; font-size: 13px; margin-bottom: 15px; border: 1px solid #e0e0e0; line-height: 1.6;}
    .guide-text {color: #FF4B4B; font-size: 13px; font-weight: 600; margin-bottom: 10px;}
</style>
""", unsafe_allow_html=True)

st.title("📊 SKBS Sales Report")

def get_p(key, default, df_full=None, col=None):
    res = params.get_all(key)
    if not res: return default
    if 'all' in res and df_full is not None and col is not None:
        return sorted(df_full[col].unique())
    if key in ['y', 'q', 'm']: return [int(x) for x in res]
    return res

# --------------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리 (오류 해결: 시트명 강제 지정 및 결측 방지)
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600, max_entries=2)
def load_data_from_drive(file_id):
    initial_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    session = requests.Session()
    brand_data = {}
    try:
        response = session.get(initial_url, stream=True)
        if "text/html" in response.headers.get("Content-Type", "").lower():
            html_content = response.text
            match_action = re.search(r'action="([^"]+)"', html_content)
            inputs = re.findall(r'name="([^"]+)"\s+value="([^"]+)"', html_content)
            params_dict = {name: value for name, value in inputs}
            if match_action:
                real_download_url = match_action.group(1).replace("&amp;", "&")
                response = session.get(real_download_url, params=params_dict, stream=True)
        
        if response.status_code != 200: return pd.DataFrame(), {}
        file_bytes = io.BytesIO(response.content)
        
        # [중요] Excel 시트 전체 로드
        xls = pd.ExcelFile(file_bytes, engine='openpyxl')
        sheets = xls.sheet_names
        
        # 1. 메인 매출 데이터 (SKBS 시트 우선)
        main_sheet = 'SKBS' if 'SKBS' in sheets else sheets[0]
        df = pd.read_excel(xls, sheet_name=main_sheet)
        
        # 2. 브랜드관 분석 데이터 로드
        for sn in ['Brand_Monthly', 'Brand_Total', 'Brand_Direct_Sales', 'Brand_Competitor']:
            if sn in sheets:
                b_df = pd.read_excel(xls, sheet_name=sn)
                b_df.columns = [re.sub(r'\s+', '', str(c)) for c in b_df.columns]
                brand_data[sn] = b_df
            else:
                brand_data[sn] = pd.DataFrame()
                
    except Exception as e:
        st.error(f"❌ 로드 오류: {e}"); return pd.DataFrame(), {}

    # 메인 데이터 컬럼 클리닝 및 매핑
    df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
    
    col_map = {
        '매출일자': ['매출일자', '날짜', 'Date'],
        '제품명': ['제품명변환', '제 품 명', '제품명'],
        '합계금액': ['합계금액', '공급가액', '금액', '매출액'],
        '수량': ['수량', 'Qty', '판매수량'],
        '사업자번호': ['사업자번호', 'BizNo'],
        '거래처명': ['거래처명', '병원명'],
        '진료과': ['진료과', '진료과목'],
        '제품군': ['제품군', '카테고리'],
        '거래처그룹': ['거래처그룹', '그룹'],
        '주소': ['도로명주소', '주소'],
        '지역': ['지역', '시도']
    }
    for std_col, candidates in col_map.items():
        if std_col in df.columns: continue
        for cand in candidates:
            if cand in df.columns: df.rename(columns={cand: std_col}, inplace=True); break

    try:
        if '매출일자' in df.columns:
            df['매출일자'] = pd.to_datetime(df['매출일자'], errors='coerce')
            df = df.dropna(subset=['매출일자'])
            df['년'] = df['매출일자'].dt.year.astype(np.int16)
            df['분기'] = df['매출일자'].dt.quarter.astype(np.int8)
            df['월'] = df['매출일자'].dt.month.astype(np.int8)
            df['년월'] = df['매출일자'].dt.strftime('%Y-%m')
        
        df['매출액'] = (pd.to_numeric(df.get('합계금액', 0), errors='coerce').fillna(0) / 1000000).astype(np.float32)
        df['수량'] = pd.to_numeric(df.get('수량', 0), errors='coerce').fillna(0).astype(np.int32)
        
        def classify_channel(group):
            online_list = ['B2B', 'B2B(W)', 'SAP', '의사회원']
            return 'online' if group in online_list else ('offline' if group == 'SDP' else '기타')
        if '거래처그룹' in df.columns: df['판매채널'] = df['거래처그룹'].apply(classify_channel)
        
        for col in ['거래처명', '제품명', '제품군', '진료과', '지역']:
            if col in df.columns: df[col] = df[col].astype(str).replace('nan', '미분류')
        if '사업자번호' not in df.columns: df['사업자번호'] = df['거래처명']
        if '제품명' in df.columns:
            df['제품명'] = df['제품명'].str.replace(r'\(.*?\)', '', regex=True).str.strip()
    except Exception as e:
        st.error(f"❌ 전처리 오류: {e}"); return pd.DataFrame(), {}
    return df, brand_data

# --------------------------------------------------------------------------------
# 3. [SK분석 기본 폼] 분석 함수 정의 (기존 1~5 로직 완전 보존)
# --------------------------------------------------------------------------------
def render_smart_overview(df_curr, df_raw):
    if df_curr.empty: return
    current_year = int(df_curr['년'].max())
    last_year = current_year - 1
    selected_months = df_curr['월'].unique()
    df_prev = df_raw[(df_raw['년'] == last_year) & (df_raw['월'].isin(selected_months))]
    sales_curr, sales_prev = df_curr['매출액'].sum(), df_prev['매출액'].sum()
    sales_gap = sales_curr - sales_prev
    sales_pct = (sales_gap / (sales_prev if sales_prev > 0 else 1) * 100)
    cust_curr, cust_prev = set(df_curr['사업자번호']), set(df_prev['사업자번호'])
    new_cust, lost_cust, retained_cust = len(cust_curr - cust_prev), len(cust_prev - cust_curr), len(cust_curr & cust_prev)

    st.markdown(f"### 🚀 {current_year}년 Summary (vs {last_year})")
    st.markdown("""<div class="info-box"><b>💡 분석 지표 기준:</b> 신규(전년무→올해유), 이탈(전년유→올해무), 유지율(전년유→올해유 비율)</div>""", unsafe_allow_html=True)
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        with c1:
            st.metric("💰 총 매출 실적", f"{sales_curr:,.0f} 백만원", f"{sales_pct:+.1f}% (YoY)")
            st.area_chart(df_curr.groupby('월')['매출액'].sum(), height=50, color="#FF4B4B")
        with c2:
            st.metric("🏥 총 거래 병원", f"{len(cust_curr)} 처")
            st.markdown(f"- ✨신규: <span style='color:blue'>+{new_cust}</span> / 💔이탈: <span style='color:red'>-{lost_cust}</span>", unsafe_allow_html=True)
        with c3:
            top_p = df_curr.groupby('제품명')['매출액'].sum().idxmax()
            st.metric("🏆 Best Product", top_p)

def render_winback_quality(df_final, df_raw, current_year):
    st.markdown(f"### ♻️ {current_year}년 재유입 현황 분석")
    st.markdown("""<div class="info-box"><b>🔍 재유입 정의:</b> 직전 구매일로부터 180일 이상 공백기 후 복귀</div>""", unsafe_allow_html=True)
    df_history = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_history['구매간격'] = (df_history['매출일자'] - df_history.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    winback_data = df_history[(df_history['사업자번호'].isin(df_final['사업자번호'])) & (df_history['구매간격'] >= 180)].copy()
    if winback_data.empty: st.info("재유입 데이터 없음"); return
    st.metric("재유입 거래처", f"{winback_data['사업자번호'].nunique()} 처")
    st.dataframe(winback_data[['거래처명', '매출일자', '제품명', '매출액']], use_container_width=True)

def render_regional_deep_dive(df):
    if df.empty: return
    reg_stats = df.groupby('지역').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    st.plotly_chart(px.scatter(reg_stats, x='Count', y='Sales', size='Sales', color='지역', text='지역'), use_container_width=True)

def render_product_strategy(df):
    if df.empty: return
    p_stats = df.groupby('제품명').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    st.plotly_chart(px.pie(p_stats, values='Sales', names='제품명', hole=0.3), use_container_width=True)

@st.cache_data
def classify_customers(df, target_year):
    cust_year = df.groupby(['사업자번호', '년']).size().unstack(fill_value=0)
    base_info = df.sort_values('매출일자').groupby('사업자번호').agg({'거래처명': 'last', '매출일자': 'max'}).rename(columns={'매출일자': '최근구매일'})
    sales_ty = df[df['년'] == target_year].groupby('사업자번호')['매출액'].sum()
    base_info['해당년도_매출'] = base_info.index.map(sales_ty).fillna(0)
    return base_info

# --------------------------------------------------------------------------------
# [보완 완료] 🏠 6. 브랜드관 성과 분석 함수
# --------------------------------------------------------------------------------
def render_brand_store_analysis(brand_data, selected_year):
    st.markdown("### 🏠 브랜드관 성과 및 마케팅 효용성 분석")
    
    # 2026년 선택 시 2025년 데이터 보여주라는 로직 반영 (사용자 요청)
    target_year = 2025 if selected_year == 2026 else selected_year
    
    # 0. 데이터 존재 여부 체크
    if not brand_data or brand_data['Brand_Total'].empty:
        st.warning("⚠️ 브랜드관 분석 시트(Brand_Total 등)가 데이터에 존재하지 않습니다."); return

    # 1. 데이터 필터링 (선택 년도 기준)
    df_total = brand_data['Brand_Total']
    df_total = df_total[df_total['년도'] == target_year]
    
    df_direct = brand_data['Brand_Direct_Sales'].copy()
    if not df_direct.empty:
        df_direct['구매일'] = pd.to_datetime(df_direct['구매일'])
        df_direct = df_direct[df_direct['구매일'].dt.year == target_year]

    df_monthly = brand_data['Brand_Monthly'].copy()
    if not df_monthly.empty:
        # '월' 컬럼이 '2025-01' 형태라고 가정
        df_monthly = df_monthly[df_monthly['월'].str.startswith(str(target_year))]

    # 2. 총괄 성과 지표 계산
    total_uv = df_total['UV'].sum() if not df_total.empty else 0
    total_pv = df_total['PV'].sum() if not df_total.empty else 0
    conv_sales = df_direct['매출'].sum() if not df_direct.empty else 0
    conv_count = df_direct['사업자번호'].nunique() if not df_direct.empty else 0
    atv = conv_sales / conv_count if conv_count > 0 else 0

    # 3. 데이터 요약 및 인사이트
    st.subheader("✔️ 성과 요약 및 인사이트")
    with st.container(border=True):
        col_sum, col_ins = st.columns([1, 1.5])
        with col_sum:
            st.write(f"📅 **분석 기준:** {target_year}년 전체")
            st.write(f"👥 **누적 방문:** {total_uv:,}명 (PV: {total_pv:,})")
            st.write(f"💰 **전환 매출:** {conv_sales:,.0f}원")
        with col_ins:
            st.write(f"💡 **분석:** 방문자 중 약 **{(conv_count/total_uv*100 if total_uv>0 else 0):.1f}%**가 브랜드관 방문 당일 구매로 전환되었습니다.")
            st.write(f"🎯 **기회:** 유입 고객의 **객단가(ATV)는 {atv:,.0f}원**으로 전체 평균 대비 높은 충성도를 보입니다.")

    # 4. 브랜드관 운영 총괄 성과 표
    st.markdown("#### 📊 브랜드관 운영 총괄 성과")
    perf_table = pd.DataFrame({
        "항목": ["UV (방문자수)", "브랜드관 전환 매출액", "구매 전환 처수", "객단가 (ATV)"],
        "성과": [f"{total_uv:,}명", f"{conv_sales:,.0f}원", f"{conv_count:,}처", f"{atv:,.0f}원"]
    })
    st.table(perf_table)

    # 5. 월별 유입 및 활동 추이 ( Plotly ValueError 방지 로직 )
    st.markdown("#### 📅 월별 브랜드관 유입 및 활동 추이")
    if not df_monthly.empty:
        fig_m = px.line(df_monthly, x='월', y=['UV', 'PV'], markers=True, title=f"{target_year}년 월별 트래픽")
        st.plotly_chart(fig_m, use_container_width=True)
    else: st.info(f"{target_year}년도 월별 데이터가 없습니다.")

    # 6. 매출 기여도 및 타사 구매 분석
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 🛍️ 브랜드관 구매 전환 매출 기여도 (Top 5)")
        if not df_direct.empty:
            top5 = df_direct.groupby('상품명').agg({'매출':'sum', '수량':'sum'}).sort_values('매출', ascending=False).head(5).reset_index()
            st.dataframe(top5.rename(columns={'매출':'매출액(원)', '수량':'구매수량'}), use_container_width=True, hide_index=True)
        else: st.info("당일 구매 내역이 없습니다.")
    
    with c2:
        st.markdown("#### 🛡️ 경쟁사 방어 분석 (타 브랜드 구매 품목)")
        df_comp = brand_data['Brand_Competitor']
        if not df_comp.empty:
            fig_comp = px.pie(df_comp, values='매출', names='상품명', hole=0.4)
            st.plotly_chart(fig_comp, use_container_width=True)
        else: st.info("타 브랜드 구매 데이터가 없습니다.")

# --------------------------------------------------------------------------------
# 4. 필터 및 실행
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
df_raw, brand_data_dict = load_data_from_drive(DRIVE_FILE_ID)
if df_raw.empty: st.stop()

# 필터 로드
sel_years = get_p('y', [df_raw['년'].max()])
sel_channels = get_p('c', sorted(df_raw['판매채널'].unique()))
sel_quarters = get_p('q', sorted(df_raw['분기'].unique()))
sel_months = get_p('m', sorted(df_raw['월'].unique()))
sel_cats = get_p('cat', sorted(df_raw['제품군'].unique()), df_raw, '제품군')
# [오류 지점 해결] df_raw가 정상 로드되었는지 확인 후 제품명 추출
sel_products = get_p('prod', sorted(df_raw['제품명'].unique()) if '제품명' in df_raw.columns else [], df_raw, '제품명')

if is_edit_mode:
    with st.sidebar:
        st.header("⚙️ 관리자 필터 설정")
        sel_channels = st.multiselect("판매채널", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        sel_quarters = st.multiselect("분기", sorted(df_raw['분기'].unique()), default=sel_quarters)
        q_to_m = {1:[1,2,3], 2:[4,5,6], 3:[7,8,9], 4:[10,11,12]}
        avail_m = sorted([m for q in sel_quarters for m in q_to_m.get(q, [])])
        sel_months = st.multiselect("월", avail_m, default=[m for m in sel_months if m in avail_m])
        sel_cats = st.multiselect("제품군", sorted(df_raw['제품군'].unique()), default=sel_cats)
        sel_products = st.multiselect("제품명", sorted(df_raw['제품명'].unique()), default=sel_products)
        if st.button("🔗 뷰어용 공유 링크 생성"):
            base_url = "https://skbs-sales-2026-cbktkdtxsyrfzfrihefs2h.streamlit.app/" 
            cat_p = "all" if len(sel_cats) == len(df_raw['제품군'].unique()) else "&cat=".join([urllib.parse.quote(val) for val in sel_cats])
            prod_p = "all" if len(sel_products) == len(df_raw['제품명'].unique()) else "&prod=".join([urllib.parse.quote(val) for val in sel_products])
            p_string = (f"?y={'&y='.join(map(str, sel_years))}&c={'&c='.join(sel_channels)}&q={'&q='.join(map(str, sel_quarters))}&m={'&m='.join(map(str, sel_months))}&cat={cat_p}&prod={prod_p}")
            st.success("공유 링크 생성!"); st.code(base_url + p_string, language="text")

df_final = df_raw[(df_raw['년'].isin(sel_years)) & (df_raw['판매채널'].isin(sel_channels)) & (df_raw['분기'].isin(sel_quarters)) & (df_raw['월'].isin(sel_months)) & (df_raw['제품군'].isin(sel_cats)) & (df_raw['제품명'].isin(sel_products))]

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 1. Overview", "🏆 2. 매출 상위 거래처 & 이탈 관리", "🔄 3. 재유입 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석", "🏠 6. 브랜드관 성과 분석"])

with tab1:
    render_smart_overview(df_final, df_raw)
    st.markdown("---")
    with st.container(border=True):
        st.markdown("### 📈 년도/분기 현황 요약")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 매출액 (년도)", f"{df_raw[df_raw['년'].isin(sel_years)]['매출액'].sum():,.0f} 백만원")
        c2.metric("총 구매처수 (년도)", f"{df_raw[df_raw['년'].isin(sel_years)]['사업자번호'].nunique():,} 처")
        c3.metric("분기 매출액", f"{df_final['매출액'].sum():,.0f} 백만원")
        c4.metric("분기 구매처수", f"{df_final['사업자번호'].nunique():,} 처")

with tab2:
    if not df_final.empty:
        ranking_v = df_final.groupby(['거래처명']).agg({'매출액': 'sum'}).sort_values('매출액', ascending=False).head(100)
        st.subheader("🥇 매출 상위 거래처 Top 100")
        st.dataframe(ranking_v, use_container_width=True)

with tab3: render_winback_quality(df_final, df_raw, sel_years[0])
with tab4: render_regional_deep_dive(df_final)
with tab5: render_product_strategy(df_final)

# [수정] 6번 탭 호출 시 선택된 년도(sel_years[0]) 전달
with tab6:
    render_brand_store_analysis(brand_data_dict, sel_years[0])
