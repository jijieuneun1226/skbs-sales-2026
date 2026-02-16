import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import urllib.parse
import numpy as np
import requests
import io
import re

# --------------------------------------------------------------------------------
# 1. 페이지 설정 및 권한 제어 (기본 틀 유지)
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
    .info-box {padding: 15px; border-radius: 5px; font-size: 14px; margin-bottom: 20px; border: 1px solid #e0e0e0; background-color: #ffffff;}
</style>
""", unsafe_allow_html=True)

st.title("📊 SKBS Sales Report")

# [필수 유틸리티] get_p 함수 상단 배치 (NameError 방지)
def get_p(key, default):
    res = params.get_all(key)
    if not res: return default
    if key in ['y', 'q', 'm']: return [int(x) for x in res]
    return res

# --------------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리 (최신 67M 우회 및 헤더 탐지 로직 통합)
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def load_data_from_drive(file_id):
    initial_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    session = requests.Session()
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
            else:
                token = next((v for k, v in response.cookies.items() if k.startswith('download_warning')), None)
                if token: response = session.get(initial_url, params={'confirm': token}, stream=True)

        if response.status_code != 200:
            st.error(f"❌ 다운로드 실패 (Code: {response.status_code})")
            return pd.DataFrame()

        file_bytes = io.BytesIO(response.content)
        df_preview = pd.read_excel(file_bytes, header=None, nrows=50, engine='openpyxl')
        target_keyword = "매출일자"
        header_row_index = -1
        for idx, row in df_preview.iterrows():
            row_str = row.astype(str).str.replace(r'\s+', '', regex=True).values
            if any(target_keyword in str(x) for x in row_str):
                header_row_index = idx
                break
        
        file_bytes.seek(0)
        df = pd.read_excel(file_bytes, header=header_row_index if header_row_index != -1 else 1, engine='openpyxl')
    except Exception as e:
        st.error(f"❌ 데이터 로드 오류: {e}"); return pd.DataFrame()

    # 컬럼명 전처리 (기본 틀 유지)
    df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
    col_map = {
        '매출일자': ['매출일자', '날짜', 'Date', '일자'],
        '제품명': ['제품명변환', '제품명', '품목명', '제 품 명'],
        '합계금액': ['합계금액', '공급가액', '금액', '매출액'],
        '수량': ['수량', 'Qty', '판매수량', '수 량'],
        '사업자번호': ['사업자번호', '사업자등록번호', 'BizNo'],
        '거래처명': ['거래처명', '병원명', '요양기관명'],
        '진료과': ['진료과', '진료과목'],
        '제품군': ['제품군', '카테고리'],
        '거래처그룹': ['거래처그룹', '그룹', '판매채널'],
        '주소': ['도로명주소', '주소'],
        '지역': ['지역', '시도']
    }
    
    current_cols = df.columns
    for std_col, candidates in col_map.items():
        if std_col in df.columns: continue
        for cand in candidates:
            clean_cand = re.sub(r'\s+', '', cand)
            if clean_cand in current_cols:
                df.rename(columns={clean_cand: std_col}, inplace=True); break

    try:
        if '주소' in df.columns: df['지역'] = df['주소'].astype(str).str.split().str[0]
        if '매출일자' in df.columns:
            df['매출일자'] = pd.to_datetime(df['매출일자'], errors='coerce')
            df = df.dropna(subset=['매출일자']).sort_values('매출일자')
            df['년'], df['분기'], df['월'] = df['매출일자'].dt.year, df['매출일자'].dt.quarter, df['매출일자'].dt.month
            df['년월'] = df['매출일자'].dt.strftime('%Y-%m')
        if '제품명' in df.columns:
            df['제품명'] = df['제품명'].str.replace(r'\(.*?\)', '', regex=True).str.strip()
        
        # [중요] 백만원 단위 표기 고정
        df['매출액'] = pd.to_numeric(df.get('합계금액', 0), errors='coerce').fillna(0) / 1000000
        df['수량'] = pd.to_numeric(df.get('수량', 0), errors='coerce').fillna(0)
        
        def classify_channel(group):
            online_list = ['B2B', 'B2B(W)', 'SAP', '의사회원']
            return 'online' if group in online_list else ('offline' if group == 'SDP' else '기타')
        if '거래처그룹' in df.columns: df['판매채널'] = df['거래처그룹'].apply(classify_channel)
        
        str_cols = ['거래처그룹', '제품명', '제품군', '진료과', '지역']
        for col in str_cols:
            if col in df.columns: df[col] = df[col].astype(str).replace('nan', '미분류')
        if '사업자번호' not in df.columns: df['사업자번호'] = df['거래처명']
    except Exception as e:
        st.error(f"❌ 전처리 오류: {e}"); return pd.DataFrame()
    return df

@st.cache_data
def classify_customers(df, target_year):
    cust_year = df.groupby(['사업자번호', '년']).size().unstack(fill_value=0)
    base_info = df.sort_values('매출일자').groupby('사업자번호').agg({
        '거래처명': 'last', '진료과': 'last', '지역': 'last', '매출일자': 'max'
    }).rename(columns={'매출일자': '최근구매일'})
    sales_ty = df[df['년'] == target_year].groupby('사업자번호')['매출액'].sum()
    base_info['해당년도_매출'] = base_info.index.map(sales_ty).fillna(0)
    
    classification = {}
    for biz_no in base_info.index:
        has_ty = (target_year in cust_year.columns) and (cust_year.loc[biz_no, target_year] > 0)
        has_t1 = (target_year - 1 in cust_year.columns) and (cust_year.loc[biz_no, target_year - 1] > 0)
        has_t2 = (target_year - 2 in cust_year.columns) and (cust_year.loc[biz_no, target_year - 2] > 0)
        has_t3 = (target_year - 3 in cust_year.columns) and (cust_year.loc[biz_no, target_year - 3] > 0)
        past_years = [y for y in cust_year.columns if y < target_year - 1]
        has_history = cust_year.loc[biz_no, past_years].sum() > 0 if past_years else False
        
        if has_ty:
            if has_t1: status = "✅ 기존 (유지)"
            else: status = "🔄 재유입 (복귀)" if (has_history or has_t2 or has_t3) else "🆕 신규 (New)"
        else:
            if has_t1: status = "📉 1년 이탈 (최근)"
            elif has_t2: status = "📉 2년 연속 이탈"
            elif has_t3: status = "📉 3년 연속 이탈"
            else: status = "💤 장기 이탈 (4년+)"
        classification[biz_no] = status
    base_info['상태'] = base_info.index.map(classification)
    return base_info

# --------------------------------------------------------------------------------
# 3. [신규 추가] 신규 분석 모듈 4가지 (요청하신 코드 그대로)
# --------------------------------------------------------------------------------

def render_smart_overview(df_curr, df_raw):
    if df_curr.empty: return
    current_year = int(df_curr['년'].max())
    last_year = current_year - 1
    selected_months = df_curr['월'].unique()
    df_prev = df_raw[(df_raw['년'] == last_year) & (df_raw['월'].isin(selected_months))]
    sales_curr, sales_prev = df_curr['매출액'].sum(), df_prev['매출액'].sum()
    sales_gap = sales_curr - sales_prev
    sales_pct = (sales_gap / sales_prev * 100) if sales_prev > 0 else 0
    cust_curr, cust_prev = set(df_curr['사업자번호']), set(df_prev['사업자번호'])
    new_cust, lost_cust, retained_cust = len(cust_curr - cust_prev), len(cust_prev - cust_curr), len(cust_curr & cust_prev)

    st.markdown(f"### 🚀 {current_year}년 Executive Summary (vs {last_year})")
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        with c1:
            st.metric("💰 총 매출 실적", f"{sales_curr:,.0f} 백만원", f"{sales_pct:+.1f}% (전년 동기 대비)")
            st.area_chart(df_curr.groupby('월')['매출액'].sum(), height=50, color="#FF4B4B")
            st.caption("👆 선택 기간 월별 매출 흐름")
        with c2:
            st.metric("🏥 총 거래 병원", f"{len(cust_curr)} 곳")
            st.markdown(f"- **✨ 신규(New):** <span style='color:blue'>+{new_cust}</span> 곳\n- **💔 이탈(Lost):** <span style='color:red'>-{lost_cust}</span> 곳", unsafe_allow_html=True)
            if len(cust_curr) > 0: st.progress(retained_cust / len(cust_curr), text=f"고객 유지율 {(retained_cust/len(cust_curr))*100:.1f}%")
        with c3:
            top_prod = df_curr.groupby('제품명')['매출액'].sum().idxmax()
            top_prod_sales = df_curr.groupby('제품명')['매출액'].sum().max()
            st.metric("🏆 Best Product", top_prod)
            st.write(f"매출 기여: **{top_prod_sales:,.0f} 백만원**")
            if lost_cust > new_cust: st.error(f"🚨 경고: 이탈이 {lost_cust-new_cust}곳 더 많습니다!")
            else: st.success(f"✅ 양호: 순증가 +{new_cust-lost_cust}곳 달성 중!")

def render_winback_quality(df, current_year):
    last_year = current_year - 1
    sales_curr = df[df['년'] == current_year].groupby(['거래처명', '지역'])['매출액'].sum()
    sales_prev = df[df['년'] == last_year].groupby(['거래처명', '지역'])['매출액'].sum()
    sales_history = df[df['년'] < current_year].groupby(['거래처명', '지역'])['매출액'].max()
    winback_list = (sales_curr.index.difference(sales_prev.index)).intersection(sales_history.index)
    if len(winback_list) == 0:
        st.info("♻️ 이번 기간에 재유입된 거래처가 없습니다."); return
    df_wb = pd.DataFrame(index=winback_list)
    df_wb['올해매출'] = sales_curr[winback_list]
    df_wb['과거최고매출'] = sales_history[winback_list]
    df_wb['회복률(%)'] = (df_wb['올해매출'] / df_wb['과거최고매출'].replace(0,1) * 100).fillna(0)
    def get_status(rate):
        if rate >= 80: return "🟢 완전 회복"
        elif rate >= 20: return "🟡 회복 중"
        else: return "🔴 간 보기 (Test)"
    df_wb['상태'] = df_wb['회복률(%)'].apply(get_status)
    df_wb = df_wb.reset_index().sort_values('올해매출', ascending=False)
    st.markdown(f"### ♻️ {current_year}년 재유입(Win-back) 현황")
    c1, c2, c3 = st.columns(3)
    c1.metric("돌아온 거래처", f"{len(df_wb)}곳")
    c2.metric("확보된 매출", f"{df_wb['올해매출'].sum():,.0f} 백만원", "이탈했다 돌아온 금액")
    c3.metric("평균 회복률", f"{df_wb['회복률(%)'].mean():.1f}%", "전성기 대비")
    st.markdown("---")
    col_ch, col_li = st.columns([1, 1])
    with col_ch:
        st.markdown("#### 📈 회복 퀄리티 매트릭스")
        try:
            fig = px.scatter(df_wb, x='과거최고매출', y='올해매출', color='상태', hover_name='거래처명', size='올해매출',
                             category_orders={"상태": ["🟢 완전 회복", "🟡 회복 중", "🔴 간 보기 (Test)"]},
                             color_discrete_map={"🟢 완전 회복": "green", "🟡 회복 중": "orange", "🔴 간 보기 (Test)": "red"})
            max_v = max(df_wb['과거최고매출'].max(), df_wb['올해매출'].max())
            fig.add_shape(type="line", x0=0, y0=0, x1=max_v, y1=max_v, line=dict(color="gray", dash="dash"))
            st.plotly_chart(fig, use_container_width=True)
        except: st.warning("차트 데이터 부족")
    with col_li:
        st.markdown("#### 📋 상세 리스트 (집중 케어 대상)")
        st.dataframe(df_wb[['상태', '거래처명', '올해매출', '회복률(%)']], hide_index=True, use_container_width=True,
                     column_config={"회복률(%)": st.column_config.ProgressColumn("회복률", format="%.1f%%", min_value=0, max_value=100), "올해매출": st.column_config.NumberColumn(format="%d 백만원")})
        n_red = len(df_wb[df_wb['상태'].astype(str).str.contains("간 보기")])
        if n_red > 0: st.warning(f"🚨 **Action:** '간 보기(🔴)' 상태인 **{n_red}곳**을 집중 관리하세요.")

def render_regional_deep_dive(df):
    if df.empty: return
    reg_stats = df.groupby('지역').agg(Total_Sales=('매출액', 'sum'), Account_Count=('사업자번호', 'nunique')).reset_index()
    reg_stats['Per_Account'] = reg_stats['Total_Sales'] / reg_stats['Account_Count']
    st.markdown("### 🗺️ 지역별 심층 효율성 분석")
    st.markdown("#### 1️⃣ 영업 효율성 매트릭스 (양 vs 질)")
    fig = px.scatter(reg_stats, x='Account_Count', y='Per_Account', size='Total_Sales', color='지역', text='지역',
                     labels={'Account_Count': '거래처 수', 'Per_Account': '병원 1곳당 평균 매출 (백만원)'})
    fig.add_hline(y=reg_stats['Per_Account'].mean(), line_dash="dash", line_color="gray", annotation_text="평균 객단가")
    fig.add_vline(x=reg_stats['Account_Count'].mean(), line_dash="dash", line_color="gray", annotation_text="평균 거래처수")
    st.plotly_chart(fig, use_container_width=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 2️⃣ 지역별 제품 선호도 (Heatmap)")
        heatmap_data = df.pivot_table(index='지역', columns='제품명', values='매출액', aggfunc='sum', fill_value=0)
        heatmap_norm = heatmap_data.div(heatmap_data.sum(axis=1), axis=0) * 100
        st.plotly_chart(px.imshow(heatmap_norm, labels=dict(x="제품", y="지역", color="매출 비중(%)"), color_continuous_scale="Blues"), use_container_width=True)
    with c2:
        st.markdown("#### 3️⃣ '골목대장' 리스크 분석")
        risk = []
        for r in df['지역'].unique():
            r_df = df[df['지역'] == r]
            top_val = r_df.groupby('거래처명')['매출액'].sum().max()
            risk.append({'지역': r, '의존도': (top_val / r_df['매출액'].sum() * 100)})
        df_risk = pd.DataFrame(risk).sort_values('의존도', ascending=False)
        st.plotly_chart(px.bar(df_risk, x='의존도', y='지역', orientation='h', color='의존도', color_continuous_scale='Reds'), use_container_width=True)

def render_product_strategy(df):
    if df.empty: return
    st.markdown("### 💊 제품별 전략 심층 분석 (Strategy Deep Dive)")
    p_stats = df.groupby('제품명').agg(Total_Sales=('매출액', 'sum'), Total_Qty=('수량', 'sum'), Account_Count=('사업자번호', 'nunique')).reset_index()
    monthly = df.groupby(['제품명', '월'])['매출액'].sum().unstack(fill_value=0)
    p_stats['Growth_Rate'] = ((monthly.iloc[:, -1] - monthly.iloc[:, 0]) / monthly.iloc[:, 0].replace(0, 1) * 100).values if monthly.shape[1] >= 2 else 0
    p_stats['Bubble_Size'] = p_stats['Total_Sales'].apply(lambda x: max(x, 0.1)) # ValueError 방지
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 1️⃣ 제품 포트폴리오 (BCG Matrix)")
        st.plotly_chart(px.scatter(p_stats, x='Growth_Rate', y='Total_Sales', size='Bubble_Size', color='제품명', text='제품명', labels={'Growth_Rate': '성장률(%)', 'Total_Sales': '매출액(백만원)'}), use_container_width=True)
    with c2:
        st.markdown("#### 5️⃣ 시장 침투율 (White Space)")
        total_acc = df['사업자번호'].nunique()
        p_stats['Penetration'] = (p_stats['Account_Count'] / total_acc) * 100
        st.plotly_chart(px.bar(p_stats.sort_values('Penetration'), x='Penetration', y='제품명', orientation='h', text_auto='.1f'), use_container_width=True)
    st.markdown("#### 3️⃣ 제품별 판매 시즌 (Seasonality)")
    season_pivot = df.pivot_table(index='제품명', columns='월', values='매출액', aggfunc='sum', fill_value=0)
    season_norm = season_pivot.div(season_pivot.max(axis=1), axis=0)
    st.plotly_chart(px.imshow(season_norm, color_continuous_scale="Reds"), use_container_width=True)

# --------------------------------------------------------------------------------
# 4. 실행 및 필터링 (기본 틀 필터 유지)
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
df_raw = load_data_from_drive(DRIVE_FILE_ID)
if df_raw.empty: st.stop()

# 사이드바 필터 설정 (기본 틀 유지)
sel_years = get_p('y', [df_raw['년'].max()])
sel_channels = get_p('c', sorted(df_raw['판매채널'].unique()))
sel_quarters = get_p('q', sorted(df_raw['분기'].unique()))
sel_months = get_p('m', sorted(df_raw['월'].unique()))

if is_edit_mode:
    with st.sidebar:
        st.header("⚙️ 관리자 필터 설정")
        sel_channels = st.multiselect("판매채널", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        sel_quarters = st.multiselect("분기", sorted(df_raw['분기'].unique()), default=sel_quarters)
        q_to_m = {1:[1,2,3], 2:[4,5,6], 3:[7,8,9], 4:[10,11,12]}
        avail_m = sorted([m for q in sel_quarters for m in q_to_m[q]])
        sel_months = st.multiselect("월", avail_m, default=[m for m in sel_months if m in avail_m])
        sel_cats = st.multiselect("제품군", sorted(df_raw['제품군'].unique()), default=sorted(df_raw['제품군'].unique()))
        sel_products = st.multiselect("제품명", sorted(df_raw['제품명'].unique()), default=sorted(df_raw['제품명'].unique()))
else:
    sel_cats, sel_products = sorted(df_raw['제품군'].unique()), sorted(df_raw['제품명'].unique())

df_final = df_raw[
    (df_raw['년'].isin(sel_years)) & (df_raw['판매채널'].isin(sel_channels)) &
    (df_raw['분기'].isin(sel_quarters)) & (df_raw['월'].isin(sel_months)) &
    (df_raw['제품군'].isin(sel_cats)) & (df_raw['제품명'].isin(sel_products))
]

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성 (기존 틀 + 신규 추가 통합)
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 1. Overview", "🏆 2. VIP & 이탈 관리", "🔄 3. 재유입 패턴 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석"])

# --- [TAB 1] Overview ---
with tab1:
    render_smart_overview(df_final, df_raw) # 🌟 신규 모듈 추가
    st.markdown("### 📈 기존 성과 요약")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 매출액 (년도)", f"{df_raw[df_raw['년'].isin(sel_years)]['매출액'].sum():,.0f}백만원")
    c2.metric("총 구매처수 (년도)", f"{df_raw[df_raw['년'].isin(sel_years)]['사업자번호'].nunique():,}처")
    c3.metric("선택기간 매출액", f"{df_final['매출액'].sum():,.0f}백만원")
    c4.metric("선택기간 구매처수", f"{df_final['사업자번호'].nunique():,}처")
    st.markdown("---")
    col_a, col_b = st.columns([1, 1.5])
    with col_a:
        st.subheader("🏥 진료과별 매출 비중")
        st.plotly_chart(px.pie(df_final, values='매출액', names='진료과', hole=0.4), use_container_width=True)
    with col_b:
        st.subheader("📅 월별 추이")
        monthly = df_final.groupby('년월').agg({'매출액': 'sum', '사업자번호': 'nunique'}).reset_index()
        fig_dual = go.Figure()
        fig_dual.add_trace(go.Bar(x=monthly['년월'], y=monthly['매출액'], name='매출(백만원)', marker_color='#a8dadc', yaxis='y1'))
        fig_dual.add_trace(go.Scatter(x=monthly['년월'], y=monthly['사업자번호'], name='구매처수(처)', line=dict(color='#e63946', width=3), yaxis='y2'))
        fig_dual.update_layout(yaxis=dict(title='매출(백만원)'), yaxis2=dict(title='구매처수(처)', overlaying='y', side='right'), legend=dict(x=0, y=1.1, orientation='h'))
        st.plotly_chart(fig_dual, use_container_width=True)

# --- [TAB 2] VIP & 이탈 관리 (기본 틀 100% 유지) ---
with tab2:
    st.markdown("### 🏆 VIP 관리 및 거래처 분류 상세 분석")
    with st.expander("🥇 매출 상위 거래처 (VIP) Top 100", expanded=True):
        if not df_final.empty:
            ranking = df_final.groupby(['사업자번호', '거래처명', '진료과']).agg({'매출액': 'sum', '수량': 'sum'}).reset_index()
            top100 = ranking.sort_values('매출액', ascending=False).head(100).copy()
            cur_date = df_raw['매출일자'].max()
            last_p = df_raw.groupby('사업자번호')['매출일자'].max()
            top100['최근구매일'] = top100['사업자번호'].map(last_p)
            top100['상태'] = top100['최근구매일'].apply(lambda x: '🚨 이탈위험' if (cur_date - x).days >= 90 else '✅ 정상')
            styled_vip = (top100[['상태', '거래처명', '진료과', '매출액', '수량']].style.format({'매출액': '{:,.1f}백만원'})
                          .applymap(lambda v: 'color: red; font-weight: bold;' if '이탈' in str(v) else '', subset=['상태']))
            event_vip = st.dataframe(styled_vip, use_container_width=True, on_select="rerun", selection_mode="single-row", height=350)
            if len(event_vip.selection.rows) > 0:
                v_idx = top100.index[event_vip.selection.rows[0]]
                st.subheader(f"🏥 [{top100.loc[v_idx, '거래처명']}] 상세 실적")
                v_detail = df_raw[df_raw['사업자번호'] == top100.loc[v_idx, '사업자번호']].groupby('제품명').agg({'수량': 'sum', '매출액': 'sum'}).reset_index()
                st.dataframe(v_detail.sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f}백만원'}), use_container_width=True)
    st.markdown("---")
    cls_df = classify_customers(df_raw, sel_years[0])
    st.plotly_chart(px.pie(cls_df, names='상태', title=f"{sel_years[0]}년 거래처 상태 분포"), use_container_width=True)

# --- [TAB 3] 재유입 패턴 분석 ---
with tab3:
    render_winback_quality(df_raw, sel_years[0]) # 🌟 신규 모듈 추가
    st.markdown("---")
    st.markdown("### 🔄 기존 재유입 패턴 (참고)")
    df_f = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_f['구매간격'] = (df_f['매출일자'] - df_f.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    res = df_final.merge(df_f[['사업자번호', '매출일자', '구매간격']], on=['사업자번호', '매출일자'], how='left')
    res = res[res['구매간격'] >= 90]
    if not res.empty:
        st.plotly_chart(px.pie(res, names='제품명', title="재유입 제품 비중"), use_container_width=True)
    else: st.info("재유입 데이터가 없습니다.")

# --- [TAB 4] 지역 분석 ---
with tab4:
    render_regional_deep_dive(df_final) # 🌟 신규 모듈 추가
    st.markdown("---")
    st.markdown("### 🗺️ 지역별 실적 및 심층 내역 (기존)")
    reg_s = df_final.groupby('지역').agg({'매출액': 'sum', '사업자번호': 'nunique'}).reset_index().rename(columns={'사업자번호': '구매처수'}).sort_values('매출액', ascending=False)
    st.dataframe(reg_s.style.format({'매출액': '{:,.0f}백만원'}), use_container_width=True)
    sel_reg = st.selectbox("🔎 지역 선택", reg_s['지역'].unique() if not reg_s.empty else [None])
    if sel_reg:
        st.plotly_chart(px.pie(df_final[df_final['지역'] == sel_reg], values='매출액', names='제품명', title=f"[{sel_reg}] 제품 비중"), use_container_width=True)

# --- [TAB 5] 제품 분석 ---
with tab5:
    render_product_strategy(df_final) # 🌟 신규 모듈 추가
    st.markdown("---")
    st.markdown("### 📦 제품별 판매 현황 (기존)")
    p_main = df_final.groupby('제품명').agg({'수량': 'sum', '매출액': 'sum', '사업자번호': 'nunique'}).reset_index().sort_values('매출액', ascending=False)
    st.dataframe(p_main.style.format({'매출액': '{:,.1f}백만원'}), use_container_width=True)
    st.plotly_chart(px.line(df_final[df_final['제품명'].isin(p_main.head(5)['제품명'])].groupby(['년월', '제품명'])['매출액'].sum().reset_index(), x='년월', y='매출액', color='제품명', title="Top 5 제품 추이"), use_container_width=True)
