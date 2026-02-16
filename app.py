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
</style>
""", unsafe_allow_html=True)

st.title("📊 SKBS Sales Report")

# --------------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리 (구글 보안 우회 + 헤더 탐색)
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def load_data_from_drive(file_id):
    URL = "https://drive.google.com/uc?export=download"
    session = requests.Session()
    try:
        response = session.get(URL, params={'id': file_id}, stream=True)
        if "text/html" in response.headers.get("Content-Type", "").lower():
            html_content = response.text
            match_action = re.search(r'action="([^"]+)"', html_content)
            inputs = re.findall(r'name="([^"]+)"\s+value="([^"]+)"', html_content)
            params_dict = {name: value for name, value in inputs}
            if match_action:
                real_url = match_action.group(1).replace("&amp;", "&")
                response = session.get(real_url, params=params_dict, stream=True)
            else:
                token = next((v for k, v in response.cookies.items() if k.startswith('download_warning')), None)
                if token: response = session.get(URL, params={'id': file_id, 'confirm': token}, stream=True)

        if response.status_code != 200:
            st.error("❌ 다운로드 실패"); return pd.DataFrame()

        file_bytes = io.BytesIO(response.content)
        df_preview = pd.read_excel(file_bytes, header=None, nrows=50, engine='openpyxl')
        target_keyword = "매출일자"
        header_row_index = -1
        for idx, row in df_preview.iterrows():
            row_str = row.astype(str).str.replace(r'\s+', '', regex=True).values
            if any(target_keyword in str(x) for x in row_str):
                header_row_index = idx; break
        
        file_bytes.seek(0)
        df = pd.read_excel(file_bytes, header=header_row_index if header_row_index != -1 else 1, engine='openpyxl')
    except Exception as e:
        st.error(f"❌ 시스템 오류: {e}"); return pd.DataFrame()

    df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
    col_map = {
        '매출일자': ['매출일자', '날짜', 'Date'],
        '제품명': ['제품명변환', '제품명', '품목명'],
        '합계금액': ['합계금액', '공급가액', '금액', '매출액'],
        '수량': ['수량', 'Qty', '판매수량', '수량'],
        '사업자번호': ['사업자번호', '사업자등록번호', 'BizNo'],
        '거래처명': ['거래처명', '병원명', '요양기관명'],
        '진료과': ['진료과', '진료과목'],
        '제품군': ['제품군', '카테고리'],
        '거래처그룹': ['거래처그룹', '그룹', '판매채널'],
        '주소': ['도로명주소', '주소'],
        '지역': ['지역', '시도']
    }
    for std_col, candidates in col_map.items():
        if std_col in df.columns: continue
        for cand in candidates:
            clean_cand = re.sub(r'\s+', '', cand)
            if clean_cand in df.columns:
                df.rename(columns={clean_cand: std_col}, inplace=True); break

    try:
        if '주소' in df.columns: df['지역'] = df['주소'].astype(str).str.split().str[0]
        if '매출일자' in df.columns:
            df['매출일자'] = pd.to_datetime(df['매출일자'], errors='coerce')
            df = df.dropna(subset=['매출일자']).sort_values('매출일자')
            df['년'], df['분기'], df['월'] = df['매출일자'].dt.year, df['매출일자'].dt.quarter, df['매출일자'].dt.month
            df['년월'] = df['매출일자'].dt.strftime('%Y-%m')
        
        df['매출액'] = pd.to_numeric(df.get('합계금액', 0), errors='coerce').fillna(0) / 1000000
        df['수량'] = pd.to_numeric(df.get('수량', 0), errors='coerce').fillna(0)
        
        def classify_channel(group):
            online_list = ['B2B', 'B2B(W)', 'SAP', '의사회원']
            return 'online' if group in online_list else ('offline' if group == 'SDP' else '기타')
        if '거래처그룹' in df.columns: df['판매채널'] = df['거래처그룹'].apply(classify_channel)
        
        for col in ['거래처명', '제품명', '제품군', '진료과', '지역']:
            if col in df.columns: df[col] = df[col].astype(str).replace('nan', '미분류')
        if '사업자번호' not in df.columns: df['사업자번호'] = df['거래처명']
        if '제품명' in df.columns: df['제품명'] = df['제품명'].str.replace(r'\(.*?\)', '', regex=True).str.strip()
    except Exception as e:
        st.error(f"❌ 전처리 오류: {e}"); return pd.DataFrame()
    return df

# --------------------------------------------------------------------------------
# 3. [복구] 거래처 상태 분류 로직 (NameError 해결용)
# --------------------------------------------------------------------------------
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
        has_history = cust_year.loc[biz_no, [y for y in cust_year.columns if y < target_year - 1]].sum() > 0 if len(cust_year.columns) > 1 else False
        
        if has_ty:
            if has_t1: status = "✅ 기존 (유지)"
            else: status = "🔄 재유입 (복귀)" if has_history else "🆕 신규 (New)"
        else:
            status = "📉 이탈"
        classification[biz_no] = status
    base_info['상태'] = base_info.index.map(classification)
    return base_info

# --------------------------------------------------------------------------------
# 4. 분석 모듈 (백만원 단위 표기 반영)
# --------------------------------------------------------------------------------
def render_smart_overview(df_curr, df_raw):
    if df_curr.empty: return
    current_year = int(df_curr['년'].max())
    last_year = current_year - 1
    selected_months = df_curr['월'].unique()
    df_prev = df_raw[(df_raw['년'] == last_year) & (df_raw['월'].isin(selected_months))]
    sales_curr, sales_prev = df_curr['매출액'].sum(), df_prev['매출액'].sum()
    sales_pct = ((sales_curr - sales_prev) / sales_prev * 100) if sales_prev > 0 else 0
    cust_curr, cust_prev = set(df_curr['사업자번호']), set(df_prev['사업자번호'])
    new_cust, lost_cust, retained_cust = len(cust_curr - cust_prev), len(cust_prev - cust_curr), len(cust_curr & cust_prev)

    st.markdown(f"### 🚀 {current_year}년 Executive Summary (vs {last_year})")
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        with c1:
            st.metric("💰 총 매출 실적", f"{sales_curr:,.0f} 백만원", f"{sales_pct:+.1f}% (YoY)")
            st.area_chart(df_curr.groupby('월')['매출액'].sum(), height=50, color="#FF4B4B")
        with c2:
            st.metric("🏥 총 거래 병원", f"{len(cust_curr)} 곳")
            st.markdown(f"- ✨신규: <span style='color:blue'>+{new_cust}</span> / 💔이탈: <span style='color:red'>-{lost_cust}</span>", unsafe_allow_html=True)
            if len(cust_curr) > 0: st.progress(retained_cust / len(cust_curr), text=f"유지율 {(retained_cust/len(cust_curr))*100:.1f}%")
        with c3:
            top_prod = df_curr.groupby('제품명')['매출액'].sum().idxmax()
            st.metric("🏆 Best Product", top_prod)
            st.write(f"기여: **{df_curr.groupby('제품명')['매출액'].sum().max():,.0f} 백만원**")

def render_winback_quality(df, current_year):
    last_year = current_year - 1
    sales_curr = df[df['년'] == current_year].groupby(['거래처명', '지역'])['매출액'].sum()
    sales_prev = df[df['년'] == last_year].groupby(['거래처명', '지역'])['매출액'].sum()
    sales_history = df[df['년'] < current_year].groupby(['거래처명', '지역'])['매출액'].max()
    winback_list = (sales_curr.index.difference(sales_prev.index)).intersection(sales_history.index)
    if len(winback_list) == 0:
        st.info("♻️ 재유입 거래처가 없습니다."); return
    df_wb = pd.DataFrame(index=winback_list)
    df_wb['올해매출'] = sales_curr[winback_list]
    df_wb['과거최고'] = sales_history[winback_list]
    df_wb['회복률'] = (df_wb['올해매출'] / df_wb['과거최고'].replace(0,1) * 100).fillna(0)
    df_wb['상태'] = df_wb['회복률'].apply(lambda x: "🟢 완전 회복" if x>=80 else ("🟡 회복 중" if x>=20 else "🔴 간 보기 (Test)"))
    df_wb = df_wb.reset_index().sort_values('올해매출', ascending=False)
    st.markdown(f"### ♻️ {current_year}년 재유입(Win-back) 현황")
    c1, c2, c3 = st.columns(3)
    c1.metric("돌아온 거래처", f"{len(df_wb)}곳")
    c2.metric("확보된 매출", f"{df_wb['올해매출'].sum():,.0f} 백만원")
    c3.metric("평균 회복률", f"{df_wb['회복률'].mean():.1f}%")
    col_ch, col_li = st.columns([1, 1])
    with col_ch:
        fig = px.scatter(df_wb, x='과거최고', y='올해매출', color='상태', hover_name='거래처명', size='올해매출',
                         category_orders={"상태": ["🟢 완전 회복", "🟡 회복 중", "🔴 간 보기 (Test)"]},
                         color_discrete_map={"🟢 완전 회복": "green", "🟡 회복 중": "orange", "🔴 간 보기 (Test)": "red"})
        fig.add_shape(type="line", x0=0, y0=0, x1=df_wb['과거최고'].max(), y1=df_wb['과거최고'].max(), line=dict(color="gray", dash="dash"))
        st.plotly_chart(fig, use_container_width=True)
    with col_li:
        st.dataframe(df_wb[['상태', '거래처명', '올해매출', '회복률']], hide_index=True, use_container_width=True,
                     column_config={"회복률": st.column_config.ProgressColumn("회복률", format="%.1f%%", min_value=0, max_value=100), "올해매출": st.column_config.NumberColumn(format="%d 백만원")})

def render_regional_deep_dive(df):
    if df.empty: return
    reg_stats = df.groupby('지역').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    reg_stats['Per'] = reg_stats['Sales'] / reg_stats['Count']
    st.markdown("### 🗺️ 지역별 심층 효율성 분석")
    fig = px.scatter(reg_stats, x='Count', y='Per', size='Sales', color='지역', text='지역', labels={'Count': '거래처 수', 'Per': '객단가(백만원)'})
    st.plotly_chart(fig, use_container_width=True)

def render_product_strategy(df):
    if df.empty: return
    st.markdown("### 💊 제품별 전략 심층 분석")
    p_stats = df.groupby('제품명').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    monthly = df.groupby(['제품명', '월'])['매출액'].sum().unstack(fill_value=0)
    p_stats['Growth'] = ((monthly.iloc[:, -1] - monthly.iloc[:, 0]) / monthly.iloc[:, 0].replace(0, 1) * 100).values if monthly.shape[1] >= 2 else 0
    p_stats['Bubble_Size'] = p_stats['Sales'].apply(lambda x: max(x, 0.1))
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(px.scatter(p_stats, x='Growth', y='Sales', size='Bubble_Size', color='제품명', text='제품명', labels={'Sales': '매출(백만원)'}), use_container_width=True)
    with c2:
        st.plotly_chart(px.bar(p_stats.sort_values('Count'), x='Count', y='제품명', orientation='h', title="제품별 구매 병원 수"), use_container_width=True)

# --------------------------------------------------------------------------------
# 5. 실행 및 필터링
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
df_raw = load_data_from_drive(DRIVE_FILE_ID)
if df_raw.empty: st.stop()

sel_years = get_p('y', [df_raw['년'].max()]) if 'params' in locals() else [df_raw['년'].max()]
sel_channels = get_p('c', sorted(df_raw['판매채널'].unique()))
sel_quarters = get_p('q', [1,2,3,4])
sel_months = get_p('m', list(range(1,13)))

if is_edit_mode:
    with st.sidebar:
        st.header("⚙️ 필터 설정")
        sel_channels = st.multiselect("판매채널", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        sel_quarters = st.multiselect("분기", [1,2,3,4], default=[1,2,3,4])
        q_to_m = {1:[1,2,3], 2:[4,5,6], 3:[7,8,9], 4:[10,11,12]}
        avail_m = sorted([m for q in sel_quarters for m in q_to_m[q]])
        sel_months = st.multiselect("월", avail_m, default=[m for m in avail_m])
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
# 6. 메인 탭
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "🏆 VIP 관리", "🔄 재유입 분석", "🗺️ 지역 분석", "📦 제품 분석"])

with tab1:
    render_smart_overview(df_final, df_raw)
    st.markdown("---")
    c1, c2 = st.columns(2)
    c1.metric("선택기간 매출액", f"{df_final['매출액'].sum():,.0f} 백만원")
    c2.metric("선택기간 구매처", f"{df_final['사업자번호'].nunique():,} 곳")
    st.plotly_chart(px.line(df_final.groupby('년월')['매출액'].sum().reset_index(), x='년월', y='매출액', title="월별 매출 추이(백만원)"), use_container_width=True)

with tab2:
    st.markdown("### 🏆 VIP 리스트 (매출액 기준)")
    if not df_final.empty:
        vip = df_final.groupby(['거래처명','진료과']).agg({'매출액':'sum'}).reset_index().sort_values('매출액', ascending=False).head(50)
        st.dataframe(vip.style.format({'매출액':'{:,.1f} 백만원'}), use_container_width=True)
    st.markdown("---")
    cls_df = classify_customers(df_raw, sel_years[0])
    st.plotly_chart(px.pie(cls_df, names='상태', title=f"{sel_years[0]}년 거래처 상태 분포"), use_container_width=True)

with tab3:
    render_winback_quality(df_raw, sel_years[0])

with tab4:
    render_regional_deep_dive(df_final)
    st.plotly_chart(px.bar(df_final.groupby('지역')['매출액'].sum().reset_index().sort_values('매출액'), x='매출액', y='지역', orientation='h', title="지역별 매출액(백만원)"), use_container_width=True)

with tab5:
    render_product_strategy(df_final)
    st.markdown("### 📦 제품별 상세 실적")
    p_df = df_final.groupby('제품명').agg({'매출액':'sum', '사업자번호':'nunique'}).reset_index().sort_values('매출액', ascending=False)
    st.dataframe(p_df.style.format({'매출액':'{:,.1f} 백만원'}), use_container_width=True)
