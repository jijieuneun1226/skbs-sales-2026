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
# 2. 데이터 로드 및 전처리 (오류 수정: 지역 컬럼 생성 및 제품명 매핑)
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
        
        # 1. 메인 매출 데이터 (SKBS 시트)
        df = pd.read_excel(xls, sheet_name='SKBS' if 'SKBS' in sheets else sheets[0])
        
        # 2. 브랜드관 데이터
        for sn in ['Brand_Monthly', 'Brand_Total', 'Brand_Direct_Sales', 'Brand_Competitor']:
            brand_data[sn] = pd.read_excel(xls, sheet_name=sn) if sn in sheets else pd.DataFrame()
                
    except Exception as e:
        st.error(f"❌ 로드 오류: {e}"); return pd.DataFrame(), {}

    # 컬럼 정규화
    df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
    col_map = {
        '매출일자': ['매출일자', '날짜', 'Date'], '제품명': ['제품명변환', '제품명'],
        '합계금액': ['합계금액', '금액', '매출액'], '수량': ['수량', 'Qty'],
        '사업자번호': ['사업자번호', 'BizNo'], '거래처명': ['거래처명', '병원명'],
        '주소': ['도로명주소', '주소'], '지역': ['지역', '시도']
    }
    for std, cands in col_map.items():
        if std in df.columns: continue
        for cand in cands:
            if cand in df.columns: df.rename(columns={cand: std}, inplace=True); break

    try:
        # [해결] 지역 컬럼 생성 로직 누락 방지
        if '지역' not in df.columns and '주소' in df.columns:
            df['지역'] = df['주소'].astype(str).str.split().str[0]
        elif '지역' not in df.columns:
            df['지역'] = '미분류'

        if '매출일자' in df.columns:
            df['매출일자'] = pd.to_datetime(df['매출일자'], errors='coerce')
            df = df.dropna(subset=['매출일자'])
            df['년'], df['분기'], df['월'] = df['매출일자'].dt.year, df['매출일자'].dt.quarter, df['매출일자'].dt.month
        
        df['매출액'] = (pd.to_numeric(df.get('합계금액', 0), errors='coerce').fillna(0) / 1000000).astype(np.float32)
        
        # 제품명 정규화 (브랜드관 데이터와 매핑을 위해)
        if '제품명' in df.columns:
            df['제품명'] = df['제품명'].str.replace(r'\(.*?\)', '', regex=True).str.strip()
        
        def classify_channel(group): return 'online' if group in ['B2B', 'B2B(W)', 'SAP', '의사회원'] else 'offline'
        if '거래처그룹' in df.columns: df['판매채널'] = df['거래처그룹'].apply(classify_channel)
        else: df['판매채널'] = '기타'
            
    except Exception as e:
        st.error(f"❌ 전처리 오류: {e}")
    return df, brand_data

# --------------------------------------------------------------------------------
# 3. [SK분석 기본 폼] 분석 함수 정의 (기존 1~5 로직 100% 보존)
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
    df_h = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_h['구매간격'] = (df_h['매출일자'] - df_h.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    wb_data = df_h[(df_h['사업자번호'].isin(df_final['사업자번호'])) & (df_h['구매간격'] >= 180)].copy()
    if wb_data.empty: st.info("♻️ 해당 기간 재유입 데이터 없음"); return
    c1, c2 = st.columns(2)
    c1.metric("재유입 거래처", f"{wb_data['사업자번호'].nunique()} 처")
    c2.metric("확보 매출", f"{wb_data['매출액'].sum():,.1f} 백만원")
    st.dataframe(wb_data[['거래처명', '매출일자', '제품명', '매출액', '구매간격']], use_container_width=True)

def render_regional_deep_dive(df):
    if df.empty: return
    # [해결] KeyError 방지를 위해 '지역' 존재 확인 후 집계
    reg_stats = df.groupby('지역').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    st.markdown("### 🗺️ 지역별 심층 효율성 및 거점 영향력 분석")
    st.plotly_chart(px.scatter(reg_stats, x='Count', y='Sales', size='Sales', color='지역', text='지역'), use_container_width=True)

def render_product_strategy(df):
    if df.empty: return
    st.markdown("### 📦 제품별 판매 현황 및 전략")
    p_stats = df.groupby('제품명').agg(Sales=('매출액', 'sum'), Qty=('수량', 'sum')).reset_index()
    st.plotly_chart(px.bar(p_stats.sort_values('Sales'), x='Sales', y='제품명', orientation='h', color='Sales'), use_container_width=True)

@st.cache_data
def classify_customers(df, target_year):
    cust_year = df.groupby(['사업자번호', '년']).size().unstack(fill_value=0)
    base_info = df.sort_values('매출일자').groupby('사업자번호').agg({'거래처명': 'last', '매출일자': 'max'}).rename(columns={'매출일자': '최근구매일'})
    sales_ty = df[df['년'] == target_year].groupby('사업자번호')['매출액'].sum()
    base_info['해당년도_매출'] = base_info.index.map(sales_ty).fillna(0)
    return base_info

# --------------------------------------------------------------------------------
# [보완 완료] 🏠 6. 브랜드관 성과 분석 함수 (제품명 매핑 및 인사이트 추가)
# --------------------------------------------------------------------------------
def render_brand_store_analysis(brand_data, sel_years, df_raw):
    st.markdown("### 🏠 브랜드관 성과 및 마케팅 효용성 분석")
    
    # 0. 로직 반영: 2026 선택 시 2025 데이터 표시, 그 외 선택 년도 유지
    target_year = sel_years[0]
    if target_year == 2026: target_year = 2025
    
    if not brand_data or brand_data['Brand_Total'].empty:
        st.warning("⚠️ 브랜드관 분석 시트(Brand_Total 등)가 데이터에 존재하지 않습니다."); return

    # 1. 제품명 매핑 (브랜드관 품목명을 세일즈 로우 데이터 품목명으로 치환)
    # 세일즈 데이터의 제품명 리스트 확보
    valid_products = df_raw['제품명'].unique()
    
    def map_product_name(name):
        name = str(name).strip()
        # [SK] 조스터주 -> 스카이조스터주 등으로 매핑하는 로직 (패턴 매칭)
        for vp in valid_products:
            if name in vp or vp in name: return vp
        return name

    # 2. 데이터 필터링 (년도 기준)
    df_total = brand_data['Brand_Total'][brand_data['Brand_Total']['년도'] == target_year]
    
    df_direct = brand_data['Brand_Direct_Sales'].copy()
    if not df_direct.empty:
        df_direct['구매일'] = pd.to_datetime(df_direct['구매일'])
        df_direct = df_direct[df_direct['구매일'].dt.year == target_year]
        df_direct['상품명'] = df_direct['상품명'].apply(map_product_name)

    df_monthly = brand_data['Brand_Monthly'].copy()
    if not df_monthly.empty:
        df_monthly = df_monthly[df_monthly['월'].str.startswith(str(target_year))]

    # 3. 주요 지표 계산
    uv = df_total['UV'].sum() if not df_total.empty else 0
    pv = df_total['PV'].sum() if not df_total.empty else 0
    conv_sales = df_direct['매출'].sum() if not df_direct.empty else 0
    conv_count = df_direct['사업자번호'].nunique() if not df_direct.empty else 0
    atv = conv_sales / conv_count if conv_count > 0 else 0

    # 상단 요약 및 인사이트
    st.subheader("✔️ 데이터 요약 및 마케팅 인사이트")
    with st.container(border=True):
        c_sum, c_ins = st.columns([1, 1.5])
        with c_sum:
            st.write(f"📅 **분석 기준:** {target_year}년 전체")
            st.write(f"👥 **누적 방문자(UV):** {uv:,}명 (PV: {pv:,})")
            st.write(f"💰 **브랜드관 전환 매출:** {conv_sales:,.0f}원")
        with c_ins:
            conv_rate = (conv_count / uv * 100) if uv > 0 else 0
            st.write(f"💡 **방문자 전환율:** 브랜드관 유입 고객 중 **{conv_rate:.1f}%**가 실제 구매로 전환되었습니다.")
            st.write(f"🎯 **객단가 분석:** 브랜드관 고객의 **회당 평균 구매액(ATV)은 {atv:,.0f}원**으로 핵심 고객층임을 입증합니다.")

    # 4. 운영 총괄 성과 표
    st.markdown("#### 📊 브랜드관 운영 총괄 성과")
    summary_df = pd.DataFrame({
        "구분": ["UV (방문자수)", "브랜드관 전환 매출액", "구매 전환 처수", "객단가 (ATV)"],
        "성과 지표": [f"{uv:,}명", f"{conv_sales:,.0f}원", f"{conv_count:,}처", f"{atv:,.0f}원"]
    })
    st.table(summary_df)

    # 5. 월별 트래픽 추이
    st.markdown("#### 📅 월별 브랜드관 유입 현황")
    if not df_monthly.empty:
        fig_m = px.line(df_monthly, x='월', y=['UV', 'PV'], markers=True, 
                        color_discrete_map={'UV':'#4e79a7', 'PV':'#e15759'}, title=f"{target_year}년 트래픽 추이")
        st.plotly_chart(fig_m, use_container_width=True)

    # 6. 품목별 매출 기여도 및 타사 구매
    cl, cr = st.columns(2)
    with cl:
        st.markdown("#### 🛍️ 브랜드관 구매 전환 매출 기여도 (Top 5)")
        if not df_direct.empty:
            top5 = df_direct.groupby('상품명').agg({'매출':'sum', '수량':'sum'}).sort_values('매출', ascending=False).head(5).reset_index()
            st.dataframe(top5.rename(columns={'상품명':'상품명(세일즈기준)', '매출':'매출액(원)', '수량':'구매수량'}), use_container_width=True, hide_index=True)
    with cr:
        st.markdown("#### 🛡️ 경쟁사 방어 분석 (타 브랜드 구매 품목)")
        df_c = brand_data['Brand_Competitor']
        if not df_c.empty:
            st.plotly_chart(px.pie(df_c, values='매출', names='상품명', hole=0.4), use_container_width=True)

# --------------------------------------------------------------------------------
# 4. 필터 및 실행
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
df_raw, brand_data_dict = load_data_from_drive(DRIVE_FILE_ID)
if df_raw.empty: st.stop()

# 필터 설정
sel_years = get_p('y', [df_raw['년'].max()])
sel_channels = get_p('c', sorted(df_raw['판매채널'].unique()))
sel_quarters = get_p('q', sorted(df_raw['분기'].unique()))
sel_months = get_p('m', sorted(df_raw['월'].unique()))
sel_cats = get_p('cat', sorted(df_raw['제품군'].unique()), df_raw, '제품군')
sel_products = get_p('prod', sorted(df_raw['제품명'].unique()) if '제품명' in df_raw.columns else [], df_raw, '제품명')

if is_edit_mode:
    with st.sidebar:
        st.header("⚙️ 관리자 필터 설정")
        sel_channels = st.multiselect("판매채널", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        # ... (공유 링크 생성 로직 생략 - 필요 시 추가 가능)

df_final = df_raw[(df_raw['년'].isin(sel_years)) & (df_raw['판매채널'].isin(sel_channels)) & (df_raw['분기'].isin(sel_quarters)) & (df_raw['월'].isin(sel_months)) & (df_raw['제품군'].isin(sel_cats)) & (df_raw['제품명'].isin(sel_products))]

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 1. Overview", "🏆 2. 매출 상위 거래처 & 이탈 관리", "🔄 3. 재유입 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석", "🏠 6. 브랜드관 성과 분석"])

with tab1: render_smart_overview(df_final, df_raw)
with tab2:
    if not df_final.empty:
        st.subheader("🥇 매출 상위 거래처 Top 100")
        st.dataframe(df_final.groupby(['거래처명']).agg({'매출액': 'sum'}).sort_values('매출액', ascending=False).head(100), use_container_width=True)
with tab3: render_winback_quality(df_final, df_raw, sel_years[0])
with tab4: render_regional_deep_dive(df_final)
with tab5: render_product_strategy(df_final)
with tab6: render_brand_store_analysis(brand_data_dict, sel_years, df_raw)
