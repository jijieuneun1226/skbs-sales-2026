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

# [수정사항 1] 공유 URL 길이를 줄이기 위해 전체 선택 시 'all'로 축약하는 로직 추가
def get_p(key, default, df_full=None, col=None):
    res = params.get_all(key)
    if not res: return default
    if 'all' in res and df_full is not None and col is not None:
        return sorted(df_full[col].unique())
    if key in ['y', 'q', 'm']: return [int(x) for x in res]
    return res

# --------------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리
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
        
        # [수정] 여러 시트를 읽기 위해 ExcelFile 사용
        xls = pd.ExcelFile(file_bytes, engine='openpyxl')
        sheets = xls.sheet_names
        
        # 매출 데이터 로드 (시트명 'SKBS' 확인)
        df = pd.read_excel(xls, sheet_name='SKBS' if 'SKBS' in sheets else 0)
        
        # 브랜드관 분석 데이터 로드 (추가된 시트들)
        for sn in ['Brand_Monthly', 'Brand_Total', 'Brand_Direct_Sales', 'Brand_Competitor']:
            if sn in sheets:
                brand_data[sn] = pd.read_excel(xls, sheet_name=sn)
            else:
                brand_data[sn] = pd.DataFrame()
                
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
        if '주소' in df.columns: df['지역'] = df['주소'].astype(str).str.split().str[0]
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
# 3. [SK분석 기본 폼] 분석 함수 정의 (기존 1~5 로직 그대로 유지)
# --------------------------------------------------------------------------------
def render_smart_overview(df_curr, df_raw):
    # (기존 코드와 동일)
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
            if len(cust_curr) > 0: st.progress(retained_cust / len(cust_curr), text=f"고객 유지율 {(retained_cust/len(cust_curr))*100:.1f}%")
        with c3:
            top_p = df_curr.groupby('제품명')['매출액'].sum().idxmax()
            st.metric("🏆 Best Product", top_p)
            st.write(f"기여: **{df_curr.groupby('제품명')['매출액'].sum().max():,.0f} 백만원**")

def render_winback_quality(df_final, df_raw, current_year):
    # (기존 코드와 동일)
    st.markdown(f"### ♻️ {current_year}년 재유입 현황 분석")
    st.markdown("""<div class="info-box"><b>🔍 재유입 정의:</b> 직전 구매일로부터 6개월 이상 공백기 후 다시 구매가 발생한 거래처</div>""", unsafe_allow_html=True)
    df_history = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_history['구매간격'] = (df_history['매출일자'] - df_history.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    winback_data = df_history[(df_history['사업자번호'].isin(df_final['사업자번호'])) & (df_history['구매간격'] >= 180)].copy()
    winback_ids = winback_data['사업자번호'].unique()
    if len(winback_ids) == 0:
        st.info("♻️ 재유입 데이터가 없습니다."); return
    sales_curr = df_final[df_final['사업자번호'].isin(winback_ids)].groupby(['사업자번호', '거래처명', '지역'])['매출액'].sum()
    sales_history = df_raw[df_raw['사업자번호'].isin(winback_ids)].groupby(['사업자번호', '거래처명', '지역'])['매출액'].max()
    df_wb = pd.DataFrame(index=sales_curr.index)
    df_wb['올해매출'], df_wb['과거최고'] = sales_curr, sales_history
    df_wb['회복률'] = (df_wb['올해매출'] / df_wb['과거최고'].replace(0,1) * 100).fillna(0)
    df_wb['상태'] = df_wb['회복률'].apply(lambda x: "완전 회복" if x>=80 else ("회복 중" if x>=20 else "재진입 초기"))
    df_wb = df_wb.reset_index().sort_values('올해매출', ascending=False)
    c1, c2, c3 = st.columns(3)
    c1.metric("재유입 거래처", f"{len(df_wb)} 처")
    c2.metric("확보 매출", f"{df_wb['올해매출'].sum():,.0f} 백만원")
    c3.metric("평균 회복률", f"{df_wb['회복률'].mean():.1f}%")
    st.dataframe(df_wb[['상태', '거래처명', '올해매출', '회복률']], use_container_width=True, hide_index=True)

def render_regional_deep_dive(df):
    # (기존 코드와 동일)
    if df.empty: return
    reg_stats = df.groupby('지역').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    reg_stats['Per'] = reg_stats['Sales'] / reg_stats['Count']
    st.markdown("### 🗺️ 지역별 심층 효율성 분석")
    fig = px.scatter(reg_stats, x='Count', y='Per', size='Sales', color='지역', text='지역')
    st.plotly_chart(fig, use_container_width=True)

def render_product_strategy(df):
    # (기존 코드와 동일)
    if df.empty: return
    st.markdown("### 💊 제품별 전략 심층 분석")
    p_stats = df.groupby('제품명').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    st.plotly_chart(px.scatter(p_stats, x='Count', y='Sales', size='Sales', color='제품명', text='제품명'), use_container_width=True)

@st.cache_data
def classify_customers(df, target_year):
    # (기존 코드와 동일)
    cust_year = df.groupby(['사업자번호', '년']).size().unstack(fill_value=0)
    base_info = df.sort_values('매출일자').groupby('사업자번호').agg({'거래처명': 'last', '진료과': 'last', '지역': 'last', '매출일자': 'max'}).rename(columns={'매출일자': '최근구매일'})
    sales_ty = df[df['년'] == target_year].groupby('사업자번호')['매출액'].sum()
    base_info['해당년도_매출'] = base_info.index.map(sales_ty).fillna(0)
    classification = {}
    for biz_no in base_info.index:
        has_ty = (target_year in cust_year.columns) and (cust_year.loc[biz_no, target_year] > 0)
        has_t1 = (target_year - 1 in cust_year.columns) and (cust_year.loc[biz_no, target_year - 1] > 0)
        past_y = [y for y in cust_year.columns if y < target_year - 1]
        has_h = cust_year.loc[biz_no, past_y].sum() > 0 if past_y else False
        if has_ty:
            if has_t1: status = "✅ 기존 (유지)"
            else: status = "🔄 재유입 (복귀)" if has_h else "🆕 신규 (New)"
        else: status = "📉 이탈"
        classification[biz_no] = status
    base_info['상태'] = base_info.index.map(classification)
    return base_info

# [추가] 🏠 6. 브랜드관 성과 분석 함수
def render_brand_store_analysis(brand_data):
    st.markdown("### 🏠 브랜드관 성과 및 마케팅 효용성 분석")
    if not brand_data or brand_data['Brand_Total'].empty:
        st.warning("⚠️ 브랜드관 분석 데이터가 없습니다. 시트명을 확인해주세요."); return

    total_df = brand_data['Brand_Total']
    total_uv, total_pv = total_df['UV'].iloc[0], total_df['PV'].iloc[0]
    direct_df = brand_data['Brand_Direct_Sales']
    purchasing_counts = direct_df['사업자번호'].nunique() if not direct_df.empty else 0
    conv_rate = (purchasing_counts / total_uv * 100) if total_uv > 0 else 0

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 방문자(UV)", f"{total_uv:,} 명")
        c2.metric("총 페이지뷰(PV)", f"{total_pv:,} 회")
        c3.metric("구매 전환 고객", f"{purchasing_counts:,} 처")
        c4.metric("최종 전환율", f"{conv_rate:.1f}%")

    st.markdown("#### 📅 월별 유입 추이")
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

df_final = df_raw[
    (df_raw['년'].isin(sel_years)) & (df_raw['판매채널'].isin(sel_channels)) &
    (df_raw['분기'].isin(sel_quarters)) & (df_raw['월'].isin(sel_months)) &
    (df_raw['제품군'].isin(sel_cats)) & (df_raw['제품명'].isin(sel_products))
]

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성 (Tab 6 추가)
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 1. Overview", "🏆 2. 매출 상위 거래처 & 이탈 관리", "🔄 3. 재유입 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석", "🏠 6. 브랜드관 성과"])

with tab1: render_smart_overview(df_final, df_raw)
with tab2:
    if not df_final.empty:
        total_s = df_final['매출액'].sum()
        ranking_v = df_final.groupby(['사업자번호', '거래처명', '진료과']).agg({'매출액': 'sum'}).sort_values('매출액', ascending=False).head(100).reset_index()
        st.subheader("🏆 상위 거래처 Top 100")
        st.dataframe(ranking_v, use_container_width=True)
with tab3: render_winback_quality(df_final, df_raw, sel_years[0])
with tab4: render_regional_deep_dive(df_final)
with tab5: render_product_strategy(df_final)
with tab6: render_brand_store_analysis(brand_data_dict)
