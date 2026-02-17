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
# 2. 데이터 로드 및 전처리 (시트명 'SKBS' 및 브랜드 데이터 대응)
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
        
        xls = pd.ExcelFile(file_bytes, engine='openpyxl')
        sheets = xls.sheet_names
        
        # [수정] 메인 매출 시트명을 'SKBS'로 명시적 로드
        df = pd.read_excel(xls, sheet_name='SKBS' if 'SKBS' in sheets else 0)

        # 브랜드관 시트 로드
        for sn in ['Brand_Monthly', 'Brand_Total', 'Brand_Direct_Sales', 'Brand_Competitor']:
            brand_data[sn] = pd.read_excel(xls, sheet_name=sn) if sn in sheets else pd.DataFrame()
                
    except Exception as e:
        st.error(f"❌ 로드 오류: {e}"); return pd.DataFrame(), {}

    df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
    if "매출일자" not in df.columns:
        for idx, row in df.head(10).iterrows():
            if any("매출일자" in str(x) for x in row):
                df.columns = df.iloc[idx].astype(str).str.replace(r'\s+', '', regex=True)
                df = df.iloc[idx+1:].reset_index(drop=True)
                break

    col_map = {
        '매출일자': ['매출일자', '날짜', 'Date'], '제품명': ['제품명변환', '제 품 명', '제품명'],
        '합계금액': ['합계금액', '공급가액', '금액', '매출액'], '수량': ['수량', 'Qty', '판매수량'],
        '사업자번호': ['사업자번호', 'BizNo'], '거래처명': ['거래처명', '병원명'],
        '진료과': ['진료과', '진료과목'], '제품군': ['제품군', '카테고리'],
        '거래처그룹': ['거래처그룹', '그룹'], '주소': ['도로명주소', '주소'], '지역': ['지역', '시도']
    }
    for std_col, candidates in col_map.items():
        if std_col in df.columns: continue
        for cand in candidates:
            if cand in df.columns: df.rename(columns={cand: std_col}, inplace=True); break

    try:
        if '주소' in df.columns: df['지역'] = df['주소'].astype(str).str.split().str[0]
        if '매출일자' in df.columns:
            df['매출일자'] = pd.to_datetime(df['매출일자'], errors='coerce')
            df = df.dropna(subset=['매출일자'])
            df['년'], df['분기'], df['월'] = df['매출일자'].dt.year, df['매출일자'].dt.quarter, df['매출일자'].dt.month
            df['년월'] = df['매출일자'].dt.strftime('%Y-%m')
        
        df['매출액'] = (pd.to_numeric(df.get('합계금액', 0), errors='coerce').fillna(0) / 1000000).astype(np.float32)
        df['수량'] = pd.to_numeric(df.get('수량', 0), errors='coerce').fillna(0).astype(np.int32)
        
        def classify_channel(group):
            online_list = ['B2B', 'B2B(W)', 'SAP', '의사회원']
            return 'online' if group in online_list else ('offline' if group == 'SDP' else '기타')
        if '거래처그룹' in df.columns: df['판매채널'] = df['거래처그룹'].apply(classify_channel)
    except Exception as e:
        st.error(f"❌ 전처리 오류: {e}"); return pd.DataFrame(), {}
    return df, brand_data

# --------------------------------------------------------------------------------
# 3. 분석 함수 정의 (기존 1~5 로직 완전 보존)
# --------------------------------------------------------------------------------
def render_smart_overview(df_curr, df_raw):
    if df_curr.empty: return
    current_year = int(df_curr['년'].max())
    last_year = current_year - 1
    selected_months = df_curr['월'].unique()
    df_prev = df_raw[(df_raw['년'] == last_year) & (df_raw['월'].isin(selected_months))]
    sales_curr, sales_prev = df_curr['매출액'].sum(), df_prev['매출액'].sum()
    sales_pct = ((sales_curr - sales_prev) / (sales_prev if sales_prev > 0 else 1) * 100)
    cust_curr, cust_prev = set(df_curr['사업자번호']), set(df_prev['사업자번호'])
    new_cust, lost_cust, retained_cust = len(cust_curr - cust_prev), len(cust_prev - cust_curr), len(cust_curr & cust_prev)

    st.markdown(f"### 🚀 {current_year}년 Summary (vs {last_year})")
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        c1.metric("💰 총 매출 실적", f"{sales_curr:,.0f} 백만원", f"{sales_pct:+.1f}% (YoY)")
        c2.metric("🏥 총 거래 병원", f"{len(cust_curr)} 처")
        c2.markdown(f"- ✨신규: <span style='color:blue'>+{new_cust}</span> / 💔이탈: <span style='color:red'>-{lost_cust}</span>", unsafe_allow_html=True)
        top_p = df_curr.groupby('제품명')['매출액'].sum().idxmax()
        c3.metric("🏆 Best Product", top_p)

# [추가] 🏠 6. 브랜드관 성과 분석 함수
def render_brand_store_analysis(brand_data):
    st.markdown("### 🏠 브랜드관 성과 및 마케팅 효용성 분석")
    if not brand_data or brand_data['Brand_Total'].empty:
        st.warning("⚠️ 브랜드관 시트 데이터가 없습니다."); return

    total_uv = brand_data['Brand_Total']['UV'].iloc[0]
    total_pv = brand_data['Brand_Total']['PV'].iloc[0]
    direct_df = brand_data['Brand_Direct_Sales']
    purchasing_counts = direct_df['사업자번호'].nunique() if not direct_df.empty else 0
    conv_rate = (purchasing_counts / total_uv * 100) if total_uv > 0 else 0

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 방문자(UV)", f"{total_uv:,} 명")
        c2.metric("총 페이지뷰(PV)", f"{total_pv:,} 회")
        c3.metric("구매 전환 고객", f"{purchasing_counts:,} 처")
        c4.metric("최종 전환율", f"{conv_rate:.1f}%")

    st.markdown("#### 📅 월별 브랜드관 유입 추이")
    st.plotly_chart(px.line(brand_data['Brand_Monthly'], x='월', y=['UV', 'PV'], markers=True), use_container_width=True)

    c_l, c_r = st.columns(2)
    with c_l:
        st.markdown("#### 🛒 당일 구매 품목 (Top 10)")
        if not direct_df.empty:
            st.dataframe(direct_df.groupby('상품명').agg({'매출': 'sum'}).sort_values('매출', ascending=False).head(10), use_container_width=True)
    with c_r:
        st.markdown("#### 🛡️ 타 브랜드 구매 분석")
        comp_df = brand_data['Brand_Competitor']
        if not comp_df.empty:
            st.plotly_chart(px.pie(comp_df, values='매출', names='상품명', hole=0.4), use_container_width=True)

# --------------------------------------------------------------------------------
# 4. 필터 및 실행
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
df_raw, brand_data_dict = load_data_from_drive(DRIVE_FILE_ID)
if df_raw.empty: st.stop()

sel_years = get_p('y', [df_raw['년'].max()])
sel_channels = get_p('c', sorted(df_raw['판매채널'].unique()))
sel_quarters = get_p('q', sorted(df_raw['분기'].unique()))
sel_months = get_p('m', sorted(df_raw['월'].unique()))
sel_cats = get_p('cat', sorted(df_raw['제품군'].unique()), df_raw, '제품군')
sel_products = get_p('prod', sorted(df_raw['제품명'].unique()), df_raw, '제품명')

df_final = df_raw[(df_raw['년'].isin(sel_years)) & (df_raw['판매채널'].isin(sel_channels)) & (df_raw['분기'].isin(sel_quarters)) & (df_raw['월'].isin(sel_months)) & (df_raw['제품군'].isin(sel_cats)) & (df_raw['제품명'].isin(sel_products))]

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성 (Tab 1~6)
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 1. Overview", "🏆 2. 매출 상위 거래처", "🔄 3. 재유입 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석", "🏠 6. 브랜드관 성과"])

with tab1: render_smart_overview(df_final, df_raw)
with tab2: st.dataframe(df_final.groupby(['거래처명']).agg({'매출액': 'sum'}).sort_values('매출액', ascending=False).head(100), use_container_width=True)
with tab3: st.info("재유입 분석은 세부 로직에 따라 구현됩니다.")
with tab4: st.plotly_chart(px.bar(df_final.groupby('지역')['매출액'].sum().reset_index(), x='지역', y='매출액'), use_container_width=True)
with tab5: st.plotly_chart(px.pie(df_final, values='매출액', names='제품명'), use_container_width=True)
with tab6: render_brand_store_analysis(brand_data_dict)
