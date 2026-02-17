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
# 2. 데이터 로드 및 전처리 (KeyError 원천 차단 보완)
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600, max_entries=2)
def load_data_from_drive(file_id):
    initial_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    session = requests.Session()
    # [수정] 모든 브랜드관 키를 미리 초기화하여 KeyError 방지
    brand_data = {'Brand_Monthly': pd.DataFrame(), 'Brand_Total': pd.DataFrame(), 'Brand_Direct_Sales': pd.DataFrame()}
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
        
        if response.status_code != 200: return pd.DataFrame(), brand_data
        file_bytes = io.BytesIO(response.content)
        xls = pd.ExcelFile(file_bytes, engine='openpyxl')
        
        # 메인 시트 로드
        df = pd.read_excel(xls, sheet_name='SKBS' if 'SKBS' in xls.sheet_names else 0)
        
        # 브랜드관 시트 로드
        for sn in brand_data.keys():
            if sn in xls.sheet_names:
                brand_data[sn] = pd.read_excel(xls, sheet_name=sn)
                
    except Exception as e:
        st.error(f"❌ 로드 오류: {e}"); return pd.DataFrame(), brand_data

    df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
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
            df['년'], df['분기'], df['월'] = df['매출일자'].dt.year.astype(np.int16), df['매출일자'].dt.quarter.astype(np.int8), df['매출일자'].dt.month.astype(np.int8)
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
    except Exception as e: st.error(f"❌ 전처리 오류: {e}"); return pd.DataFrame(), brand_data
    return df, brand_data

# --------------------------------------------------------------------------------
# 3. [원본 100% 동일] 분석 함수 정의 (1~5번 탭 로직 성역)
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
            if len(cust_curr) > 0: st.progress(retained_cust / len(cust_curr), text=f"고객 유지율 {(retained_cust/len(cust_curr))*100:.1f}%")
        with c3:
            top_p = df_curr.groupby('제품명')['매출액'].sum().idxmax()
            st.metric("🏆 Best Product", top_p)
            st.write(f"기여: **{df_curr.groupby('제품명')['매출액'].sum().max():,.0f} 백만원**")

def render_winback_quality(df_final, df_raw, current_year):
    st.markdown(f"### ♻️ {current_year}년 재유입 현황 분석")
    st.markdown("""<div class="info-box"><b>🔍 재유입 정의:</b> 직전 구매일로부터 180일 이상 공백기 후 복귀</div>""", unsafe_allow_html=True)
    df_history = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_history['구매간격'] = (df_history['매출일자'] - df_history.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    winback_data = df_history[(df_history['사업자번호'].isin(df_final['사업자번호'])) & (df_history['구매간격'] >= 180)].copy()
    winback_ids = winback_data['사업자번호'].unique()
    if len(winback_ids) == 0: st.info("♻️ 재유입 데이터가 없습니다."); return
    sales_curr = df_final[df_final['사업자번호'].isin(winback_ids)].groupby(['사업자번호', '거래처명', '지역'])['매출액'].sum()
    sales_history = df_raw[df_raw['사업자번호'].isin(winback_ids)].groupby(['사업자번호', '거래처명', '지역'])['매출액'].max()
    df_wb = pd.DataFrame(index=sales_curr.index)
    df_wb['올해매출'], df_wb['과거최고'] = sales_curr, sales_history
    df_wb['회복률'] = (df_wb['올해매출'] / df_wb['과거최고'].replace(0,1) * 100).fillna(0)
    df_wb['상태'] = df_wb['회복률'].apply(lambda x: "완전 회복" if x>=80 else ("회복 중" if x>=20 else "재진입 초기"))
    df_wb = df_wb.reset_index().sort_values('올해매출', ascending=False)
    c1, c2, c3 = st.columns(3)
    c1.metric("재유입 거래처", f"{len(df_wb)} 처"); c2.metric("확보 매출", f"{df_wb['올해매출'].sum():,.0f} 백만원"); c3.metric("평균 회복률", f"{df_wb['회복률'].mean():.1f}%")
    col_ch, col_li = st.columns([1, 1])
    with col_ch:
        fig = px.scatter(df_wb, x='과거최고', y='올해매출', color='상태', hover_name='거래처명', size=df_wb['올해매출'].apply(lambda x: max(x, 0.1)),
                         category_orders={"상태": ["완전 회복", "회복 중", "재진입 초기"]},
                         color_discrete_map={"완전 회복": "green", "회복 중": "orange", "재진입 초기": "red"})
        st.plotly_chart(fig, use_container_width=True)
    with col_li:
        event_wb = st.dataframe(df_wb[['상태', '거래처명', '올해매출', '회복률']], hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row",
                               column_config={"회복률": st.column_config.ProgressColumn("회복도", format="%.1f%%", min_value=0, max_value=100), "올해매출": st.column_config.NumberColumn(format="%.1f 백만원")})
    if len(event_wb.selection.rows) > 0:
        sel_idx = event_wb.selection.rows[0]
        sel_biz_no, sel_name = df_wb.iloc[sel_idx]['사업자번호'], df_wb.iloc[sel_idx]['거래처명']
        st.markdown(f"#### 🔍 [{sel_name}] 실제 구매 간격 및 상세 내역")
        detail_hist = df_history[df_history['사업자번호'] == sel_biz_no].sort_values('매출일자', ascending=False).copy()
        detail_hist['매출일자_str'] = detail_hist['매출일자'].dt.strftime('%Y-%m-%d')
        st.dataframe(detail_hist[['매출일자_str', '제품명', '매출액', '수량', '구매간격']].rename(columns={'매출일자_str':'매출일자', '구매간격':'직전구매후공백(일)'})
                     .style.applymap(lambda v: 'background-color: #ffcccc; font-weight: bold;' if isinstance(v, (int, float)) and v >= 180 else '', subset=['직전구매후공백(일)'])
                     .format({'매출액': '{:,.1f} 백만원'}), use_container_width=True)

def render_regional_deep_dive(df):
    if df.empty: return
    reg_stats = df.groupby('지역').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    reg_stats['Per'] = reg_stats['Sales'] / reg_stats['Count']
    st.markdown("### 🗺️ 지역별 심층 효율성 및 거점 영향력 분석")
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.metric("최고 매출 지역", reg_stats.loc[reg_stats['Sales'].idxmax(), '지역'])
        c2.metric("영업 효율 1위", reg_stats.loc[reg_stats['Per'].idxmax(), '지역'], f"{reg_stats['Per'].max():.1f} 백만원/처")
        c3.metric("활성 지역 수", f"{len(reg_stats)} 개")
    st.plotly_chart(px.scatter(reg_stats, x='Count', y='Per', size='Sales', color='지역', text='지역'), use_container_width=True)
    c1, c2 = st.columns(2)
    with c1: st.plotly_chart(px.pie(reg_stats, values='Sales', names='지역', hole=0.3, title="지역별 매출 기여도 (%)"), use_container_width=True)
    with c2:
        risk = []
        for r in df['지역'].unique():
            r_df = df[df['지역'] == r]
            risk.append({'지역': r, '의존도': (r_df.groupby('거래처명')['매출액'].sum().max() / r_df['매출액'].sum() * 100)})
        st.plotly_chart(px.bar(pd.DataFrame(risk).sort_values('의존도', ascending=False), x='의존도', y='지역', orientation='h', color='의존도', color_continuous_scale='Reds', title="핵심 거점 매출 의존도 (%)"), use_container_width=True)

def render_product_strategy(df):
    if df.empty: return
    st.markdown("### 💊 제품별 전략 심층 분석")
    p_stats = df.groupby('제품명').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    p_stats['Bubble_Size'] = p_stats['Sales'].apply(lambda x: max(x, 0.1))
    c1, c2 = st.columns(2)
    with c1: st.plotly_chart(px.scatter(p_stats, x='Count', y='Sales', size='Bubble_Size', color='제품명', text='제품명', title="BCG 매트릭스"), use_container_width=True)
    with c2: st.plotly_chart(px.bar(p_stats.sort_values('Count'), x=(p_stats['Count'] / df['사업자번호'].nunique() * 100), y='제품명', orientation='h', text_auto='.1f', title="시장 침투율 (%)"), use_container_width=True)
    st.markdown("#### 📅 제품별 판매 시즌 집중도 (Seasonality)")
    season_pivot = df.pivot_table(index='제품명', columns='월', values='매출액', aggfunc='sum', fill_value=0)
    st.plotly_chart(px.imshow(season_pivot.div(season_pivot.max(axis=1), axis=0), color_continuous_scale="Reds", aspect="auto"), use_container_width=True)
    with st.expander("🧩 함께 팔기(Cross-selling) 기회 분석기", expanded=True):
        col_s1, col_s2 = st.columns(2)
        all_p = sorted(df['제품명'].unique())
        with col_s1: base_p = st.selectbox("Anchor 제품", all_p, index=0)
        with col_s2: target_p = st.selectbox("Target 제품", all_p, index=min(1, len(all_p)-1))
        if base_p != target_p:
            acc_A, acc_B = set(df[df['제품명'] == base_p]['거래처명'].unique()), set(df[df['제품명'] == target_p]['거래처명'].unique())
            targets = list(acc_A - acc_B)
            st.metric("🎯 추가 영업 기회", f"{len(targets)} 처")
            if targets:
                t_info = df[(df['거래처명'].isin(targets)) & (df['제품명'] == base_p)].groupby(['거래처명', '지역'])['매출액'].sum().reset_index().sort_values('매출액', ascending=False)
                st.dataframe(t_info.head(50), column_config={"매출액": st.column_config.NumberColumn("구매액(백만원)", format="%.1f")}, hide_index=True)

@st.cache_data
def classify_customers(df, target_year):
    cust_year = df.groupby(['사업자번호', '년']).size().unstack(fill_value=0)
    base_info = df.sort_values('매출일자').groupby('사업자번호').agg({'거래처명': 'last', '진료과': 'last', '지역': 'last', '매출일자': 'max'}).rename(columns={'매출일자': '최근구매일'})
    sales_ty = df[df['년'] == target_year].groupby('사업자번호')['매출액'].sum()
    base_info['해당년도_매출'] = base_info.index.map(sales_ty).fillna(0)
    classification = {biz: ("✅ 기존 (유지)" if (target_year-1 in cust_year.columns and cust_year.loc[biz, target_year-1]>0) else "🆕 신규 (New)") if (target_year in cust_year.columns and cust_year.loc[biz, target_year]>0) else "📉 이탈" for biz in base_info.index}
    base_info['상태'] = base_info.index.map(classification)
    return base_info

# --------------------------------------------------------------------------------
# [KeyError 해결] 🏠 6. 브랜드관 성과 분석 (사용자 요청 디자인 완벽 반영)
# --------------------------------------------------------------------------------
def render_brand_store_analysis(brand_data, sel_years, df_raw):
    st.markdown("### 🏠 브랜드관 성과 및 마케팅 효용성 분석")
    target_year = sel_years[0]
    
    # 데이터 유무 확인 강화
    if brand_data['Brand_Total'].empty:
        st.warning("🏠 브랜드관 분석 데이터가 없습니다."); return

    # 제품명 매핑
    valid_p = df_raw['제품명'].unique()
    def map_p(n):
        c = str(n).replace('[SK]', '').replace('주', '').replace('0.5ml', '').strip()
        for vp in valid_p:
            if c[:5] in vp: return vp
        return str(n).strip()
    
    df_t = brand_data['Brand_Total'][brand_data['Brand_Total']['년도'] == target_year]
    df_d = brand_data['Brand_Direct_Sales'].copy()
    if not df_d.empty:
        df_d['구매일'] = pd.to_datetime(df_d['구매일'])
        df_d = df_d[df_d['구매일'].dt.year == target_year]
        df_d['상품명_매핑'] = df_d['상품명'].apply(map_p)
        df_d['매출_백만'] = df_d['매출'] / 1000000

    df_m = brand_data['Brand_Monthly'].copy()
    if not df_m.empty: df_m = df_m[df_m['월'].astype(str).str.startswith(str(target_year))]

    uv = df_t['UV'].sum() if not df_t.empty else 0
    sales_m = df_d['매출_백만'].sum() if not df_d.empty else 0
    count = df_d['사업자번호'].nunique() if not df_d.empty else 0
    atv = (sales_m * 1000000 / count) if count > 0 else 0

    st.markdown(f"### 🚀 브랜드관 성과 Summary ({target_year}년)")
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        with c1:
            st.metric("💰 브랜드관 전환 매출", f"{sales_m:,.1f} 백만원")
            if not df_m.empty: st.area_chart(df_m.set_index('월')['UV'], height=50, color="#4e79a7")
        with c2:
            st.metric("🏥 구매 전환 고객", f"{count} 처")
            if uv > 0: st.progress(count / uv, text=f"방문자 전환율 {(count/uv*100):.1f}%")
        with c3:
            st.metric("💳 평균 객단가 (ATV)", f"{atv:,.0f} 원")

    st.markdown("#### 📊 브랜드관 운영 총괄 성과")
    st.table(pd.DataFrame({"구분": ["UV (방문자수)", "브랜드관 전환 매출액", "구매 전환 처수", "객단가 (ATV)"], "성과 지표": [f"{uv:,} 명", f"{sales_m:,.1f} 백만원", f"{count:,} 처", f"{atv:,.0f} 원"]}))

    col_l, col_r = st.columns([1.5, 1])
    with col_l:
        st.markdown("#### 📅 월별 유입 추이")
        if not df_m.empty: st.plotly_chart(px.line(df_m, x='월', y=['UV', 'PV'], markers=True), use_container_width=True)
    with col_r:
        st.markdown("#### 🏥 전환 진료과 비중")
        if not df_d.empty: st.plotly_chart(px.pie(df_d, values='매출', names='진료과', hole=0.4), use_container_width=True)

# --------------------------------------------------------------------------------
# 4. 필터 및 실행
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
data_res = load_data_from_drive(DRIVE_FILE_ID)
if not data_res: st.stop()
df_raw, brand_data_dict = data_res

sel_years = get_p('y', [df_raw['년'].max()])
sel_channels = get_p('c', sorted(df_raw['판매채널'].unique()))
sel_quarters = get_p('q', sorted(df_raw['분기'].unique()))
sel_months = get_p('m', sorted(df_raw['월'].unique()))
sel_cats = get_p('cat', sorted(df_raw['제품군'].unique()), df_raw, '제품군')
sel_products = get_p('prod', sorted(df_raw['제품명'].unique()), df_raw, '제품명')

if is_edit_mode:
    with st.sidebar:
        st.header("⚙️ 관리자 필터 설정")
        sel_channels = st.multiselect("판매채널", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        sel_quarters = st.multiselect("분기", sorted(df_raw['분기'].unique()), default=sel_quarters)
        avail_m = sorted([m for q in sel_quarters for m in {1:[1,2,3], 2:[4,5,6], 3:[7,8,9], 4:[10,11,12]}.get(q, [])])
        sel_months = st.multiselect("월", avail_m, default=[m for m in sel_months if m in avail_m])
        sel_cats = st.multiselect("제품군", sorted(df_raw['제품군'].unique()), default=sel_cats)
        sel_products = st.multiselect("제품명", sorted(df_raw['제품명'].unique()), default=sel_products)

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
        col_a, col_b = st.columns([1, 1.5])
        with col_a: st.plotly_chart(px.pie(df_final, values='매출액', names='진료과', hole=0.4, title="진료과별 매출 비중"), use_container_width=True)
        with col_b: st.plotly_chart(px.bar(df_final.groupby('년월')['매출액'].sum().reset_index(), x='년월', y='매출액', text_auto='.1f', title="월별 매출 추이", color_discrete_sequence=['#a8dadc']), use_container_width=True)

with tab2:
    if not df_final.empty:
        ranking_v = df_final.groupby(['사업자번호', '거래처명', '진료과']).agg({'매출액': 'sum'}).sort_values('매출액', ascending=False).head(100).reset_index()
        cls_d = classify_customers(df_raw, sel_years[0])
        st_c = cls_d['상태'].value_counts()
        last_p = df_raw.groupby('사업자번호')['매출일자'].max()
        ranking_v['최근구매일'] = ranking_v['사업자번호'].map(last_p)
        cur_d = df_raw['매출일자'].max()
        risk_cnt = len(ranking_v[(cur_d - ranking_v['최근구매일']).dt.days >= 90])
        top_v = ranking_v.iloc[0]
        st.subheader("✔️ Summary")
        st.write(f"• 상위 100처 매출 합계: **{ranking_v['매출액'].sum()/100:,.1f}억원**")
        st.write(f"• 거래처 상태 분포: **기존({st_c.get('✅ 기존 (유지)',0)}처), 신규({st_c.get('🆕 신규 (New)',0)}처), 이탈({st_c.get('📉 이탈',0)}처)**")
        st.subheader("✔️ Insight")
        st.write(f"• **VIP 이탈 위험 알림:** 현재 상위 100처 중 **{risk_cnt}처**가 90일 이상 구매가 없습니다. 특히 **{top_v['거래처명']}**의 관리가 시급합니다.")
        st.markdown("---")
        event_vip = st.dataframe(ranking_v[['거래처명', '진료과', '매출액']], use_container_width=True, on_select="rerun", selection_mode="single-row", height=350)
        if len(event_vip.selection.rows) > 0:
            v_biz = ranking_v.iloc[event_vip.selection.rows[0]]['사업자번호']
            st.dataframe(df_raw[df_raw['사업자번호'] == v_biz].groupby('제품명').agg({'매출액': 'sum'}).sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True)

with tab3: render_winback_quality(df_final, df_raw, sel_years[0])
with tab4: render_regional_deep_dive(df_final)
with tab5:
    p_stats_v5 = df_final.groupby('제품명').agg(Sales=('매출액', 'sum')).reset_index().sort_values('Sales', ascending=False)
    st.markdown("### 📦 제품별 판매 현황")
    c_p1, c_p2 = st.columns(2)
    with c_p1: st.plotly_chart(px.bar(p_stats_v5, x='Sales', y='제품명', orientation='h', title="제품별 매출 현황", color='Sales'), use_container_width=True)
    with c_p2: render_product_strategy(df_final)
    p_main_v = df_final.groupby('제품명').agg({'수량': 'sum', '매출액': 'sum', '사업자번호': 'nunique'}).reset_index().sort_values('매출액', ascending=False)
    ev_p_v = st.dataframe(p_main_v.rename(columns={'사업자번호':'구매처수'}).style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True, on_select="rerun", selection_mode="single-row", height=300)
    if len(ev_p_v.selection.rows) > 0:
        sel_p_v = p_main_v.iloc[ev_p_v.selection.rows[0]]['제품명']
        st.dataframe(df_final[df_final['제품명'] == sel_p_v].groupby('거래처명').agg({'매출액': 'sum'}).reset_index().sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True)

with tab6: render_brand_store_analysis(brand_data_dict, sel_years, df_raw)
