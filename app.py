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
    .info-box {padding: 15px; border-radius: 5px; font-size: 13px; margin-bottom: 20px; border: 1px solid #e0e0e0; background-color: #fcfcfc; line-height: 1.6;}
    .guide-text {color: #FF4B4B; font-size: 13px; font-weight: 600; margin-bottom: 10px;}
</style>
""", unsafe_allow_html=True)

st.title("📊 SKBS Sales Report")

def get_p(key, default):
    res = params.get_all(key)
    if not res: return default
    if key in ['y', 'q', 'm']: return [int(x) for x in res]
    return res

# --------------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리
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
                real_url = match_action.group(1).replace("&amp;", "&")
                response = session.get(real_url, params=params_dict, stream=True)
            else:
                token = next((v for k, v in response.cookies.items() if k.startswith('download_warning')), None)
                if token: response = session.get(initial_url, params={'confirm': token}, stream=True)

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
        st.error(f"❌ 데이터 로드 오류: {e}"); return pd.DataFrame()

    df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
    col_map = {
        '매출일자': ['매출일자', '날짜', 'Date', '일자'],
        '제품명': ['제품명변환', '제 품 명', '제품명', '품목명'],
        '합계금액': ['합계금액', '공급가액', '금액', '매출액'],
        '수량': ['수량', 'Qty', '판매수량', '수 량'],
        '사업자번호': ['사업자번호', '사업자등록번호', 'BizNo'],
        '거래처명': ['거래처명', '병원명', '요양기관명'],
        '진료과': ['진료과', '진료과목'],
        '제품군': ['제품군', '카테고리'],
        '거래처그룹': ['거래처그룹', '그룹', '판매채널'],
        '주소': ['도로명주소', '주소', '사업장주소'],
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
        if '제품명' in df.columns:
            df['제품명'] = df['제품명'].str.replace(r'\(.*?\)', '', regex=True).str.strip()
        df['매출액'] = pd.to_numeric(df.get('합계금액', 0), errors='coerce').fillna(0) / 1000000
        df['수량'] = pd.to_numeric(df.get('수량', 0), errors='coerce').fillna(0)
        def classify_channel(group):
            online_list = ['B2B', 'B2B(W)', 'SAP', '의사회원']
            return 'online' if group in online_list else ('offline' if group == 'SDP' else '기타')
        if '거래처그룹' in df.columns: df['판매채널'] = df['거래처그룹'].apply(classify_channel)
        for col in ['거래처명', '제품명', '제품군', '진료과', '지역']:
            if col in df.columns: df[col] = df[col].astype(str).replace('nan', '미분류')
        if '사업자번호' not in df.columns: df['사업자번호'] = df['거래처명']
    except Exception as e:
        st.error(f"❌ 전처리 오류: {e}"); return pd.DataFrame()
    return df

@st.cache_data
def classify_customers(df, target_year):
    cust_year = df.groupby(['사업자번호', '년']).size().unstack(fill_value=0)
    base_info = df.sort_values('매출일자').groupby('사업자번호').agg({'거래처명': 'last', '진료과': 'last', '지역': 'last', '매출일자': 'max'}).rename(columns={'매출일자': '최근구매일'})
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
        else: status = "📉 이탈"
        classification[biz_no] = status
    base_info['상태'] = base_info.index.map(classification)
    return base_info

# --------------------------------------------------------------------------------
# 3. 분석 모듈 정의
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
    st.markdown(f"""<div class="info-box">
    <b>💡 분석 지표 기준:</b><br>
    - <b>신규:</b> 전년 동기에는 구매가 없었으나 올해 새롭게 유입된 병원<br>
    - <b>이탈:</b> 전년 동기에는 구매했으나 올해 구매가 끊긴 병원<br>
    - <b>유지율:</b> 전년 동기 구매자 중 올해도 유지된 비중 (Retained / 전년 총 거래처)
    </div>""", unsafe_allow_html=True)
    
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        with c1:
            st.metric("💰 총 매출 실적", f"{sales_curr:,.0f} 백만원", f"{sales_pct:+.1f}% (YoY)")
            st.area_chart(df_curr.groupby('월')['매출액'].sum(), height=50, color="#FF4B4B")
        with c2:
            st.metric("🏥 총 거래 병원", f"{len(cust_curr)} 곳")
            st.markdown(f"- ✨신규: <span style='color:blue'>+{new_cust}</span> / 💔이탈: <span style='color:red'>-{lost_cust}</span>", unsafe_allow_html=True)
            if len(cust_curr) > 0: st.progress(retained_cust / len(cust_curr), text=f"고객 유지율 {(retained_cust/len(cust_curr))*100:.1f}%")
        with c3:
            top_p_name = df_curr.groupby('제품명')['매출액'].sum().idxmax()
            st.metric("🏆 Best Product", top_p_name)
            st.write(f"기여: **{df_curr.groupby('제품명')['매출액'].sum().max():,.0f} 백만원**")

def render_winback_quality(df, current_year):
    last_year = current_year - 1
    sales_curr = df[df['년'] == current_year].groupby(['거래처명', '지역'])['매출액'].sum()
    sales_prev = df[df['년'] == last_year].groupby(['거래처명', '지역'])['매출액'].sum()
    sales_history = df[df['년'] < current_year].groupby(['거래처명', '지역'])['매출액'].max()
    winback_list = (sales_curr.index.difference(sales_prev.index)).intersection(sales_history.index)
    
    st.markdown(f"### ♻️ {current_year}년 재유입(Win-back) 현황 분석")
    st.markdown("""<div class="info-box">
    <b>🔍 재유입 정의:</b> 직전 연도(공백기) 구매가 전혀 없었으나, 과거 구매 이력이 있는 거래처가 올해 복귀한 경우<br>
    <b>🚦 회복 퀄리티 등급 (과거 전성기 대비 올해 매출 비중):</b><br>
    - 🟢 <b>완전 회복:</b> 80% 이상 / 🟡 <b>회복 중:</b> 20~80% / 🔴 <b>초기 반응(Test):</b> 20% 미만 (집중 케어 필요)
    </div>""", unsafe_allow_html=True)

    if len(winback_list) == 0:
        st.info("♻️ 해당 기간에 재유입된 거래처가 없습니다."); return

    df_wb = pd.DataFrame(index=winback_list)
    df_wb['올해매출'] = sales_curr[winback_list]
    df_wb['과거최고'] = sales_history[winback_list]
    df_wb['회복률'] = (df_wb['올해매출'] / df_wb['과거최고'].replace(0,1) * 100).fillna(0)
    
    def get_status_str(rate):
        if rate >= 80: return "완전 회복"
        elif rate >= 20: return "회복 중"
        else: return "초기 반응(Test)"
        
    df_wb['상태'] = df_wb['회복률'].apply(get_status_str)
    df_wb = df_wb.reset_index().sort_values('올해매출', ascending=False)
    df_wb['Bubble_Size'] = df_wb['올해매출'].apply(lambda x: max(x, 0.1))

    c1, c2, c3 = st.columns(3)
    c1.metric("돌아온 거래처", f"{len(df_wb)}곳")
    c2.metric("확보된 매출", f"{df_wb['올해매출'].sum():,.0f} 백만원")
    c3.metric("평균 회복률", f"{df_wb['회복률'].mean():.1f}%")
    
    col_ch, col_li = st.columns([1, 1])
    with col_ch:
        try:
            fig = px.scatter(df_wb, x='과거최고', y='올해매출', color='상태', hover_name='거래처명', size='Bubble_Size',
                             category_orders={"상태": ["완전 회복", "회복 중", "초기 반응(Test)"]},
                             color_discrete_map={"완전 회복": "green", "회복 중": "orange", "초기 반응(Test)": "red"})
            fig.add_shape(type="line", x0=0, y0=0, x1=df_wb['과거최고'].max(), y1=df_wb['과거최고'].max(), line=dict(color="gray", dash="dash"))
            st.plotly_chart(fig, use_container_width=True)
        except: st.warning("차트 데이터 형식 오류")
    with col_li:
        st.markdown('<p class="guide-text">💡 아래 리스트의 행을 클릭하면 상세 구매 이력이 연동됩니다.</p>', unsafe_allow_html=True)
        st.dataframe(df_wb[['상태', '거래처명', '올해매출', '회복률']], hide_index=True, use_container_width=True,
                     column_config={"회복률": st.column_config.ProgressColumn("회복도", format="%.1f%%", min_value=0, max_value=100), "올해매출": st.column_config.NumberColumn(format="%d 백만원")})

def render_regional_deep_dive(df):
    if df.empty: return
    reg_stats = df.groupby('지역').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    reg_stats['Per'] = reg_stats['Sales'] / reg_stats['Count']
    
    st.markdown("### 🗺️ 지역별 심층 효율성 및 거점 영향력 분석")
    st.markdown(f"""<div class="info-box">
    <b>📈 지역 전략 요약:</b><br>
    - 현재 가장 매출이 높은 지역은 <b>{reg_stats.loc[reg_stats['Sales'].idxmax(), '지역']}</b>이며, 
    평균 객단가(영업 효율)가 가장 높은 곳은 <b>{reg_stats.loc[reg_stats['Per'].idxmax(), '지역']}</b>({reg_stats['Per'].max():.1f}M)입니다.
    </div>""", unsafe_allow_html=True)

    fig = px.scatter(reg_stats, x='Count', y='Per', size='Sales', color='지역', text='지역', 
                     labels={'Count': '거래처 수', 'Per': '평균 객단가 (백만원)'})
    fig.add_hline(y=reg_stats['Per'].mean(), line_dash="dash", line_color="gray", annotation_text="평균 효율")
    fig.add_vline(x=reg_stats['Count'].mean(), line_dash="dash", line_color="gray", annotation_text="평균 커버리지")
    st.plotly_chart(fig, use_container_width=True)
    
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 지역별 매출 기여도 (%)")
        st.plotly_chart(px.pie(reg_stats, values='Sales', names='지역', hole=0.3), use_container_width=True)
    with c2:
        st.markdown("#### 핵심 거점 의존도 분석")
        risk = []
        for r in df['지역'].unique():
            r_df = df[df['지역'] == r]
            top_hospital = r_df.groupby('거래처명')['매출액'].sum().max()
            risk.append({'지역': r, '의존도': (top_hospital / r_df['매출액'].sum() * 100)})
        df_risk = pd.DataFrame(risk).sort_values('의존도', ascending=False)
        st.plotly_chart(px.bar(df_risk, x='의존도', y='지역', orientation='h', color='의존도', 
                               color_continuous_scale='Reds', title="지역 내 1위 병원 매출 비중 (%)"), use_container_width=True)

def render_product_strategy(df):
    if df.empty: return
    st.markdown("### 💊 제품별 전략 심층 분석 (Strategy Deep Dive)")
    p_stats = df.groupby('제품명').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    monthly = df.groupby(['제품명', '월'])['매출액'].sum().unstack(fill_value=0)
    p_stats['Growth'] = ((monthly.iloc[:, -1] - monthly.iloc[:, 0]) / monthly.iloc[:, 0].replace(0, 1) * 100).values if monthly.shape[1] >= 2 else 0
    p_stats['Bubble_Size'] = p_stats['Sales'].apply(lambda x: max(x, 0.1))
    
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 1️⃣ 제품 포트폴리오 (BCG Matrix)")
        st.plotly_chart(px.scatter(p_stats, x='Growth', y='Sales', size='Bubble_Size', color='제품명', text='제품명', labels={'Growth': '성장률(%)', 'Sales': '매출액(백만원)'}), use_container_width=True)
    with c2:
        st.markdown("#### 5️⃣ 시장 침투율 (White Space)")
        total_acc = df['사업자번호'].nunique()
        p_stats['Penetration'] = (p_stats['Count'] / total_acc) * 100
        st.plotly_chart(px.bar(p_stats.sort_values('Penetration'), x='Penetration', y='제품명', orientation='h', text_auto='.1f', title=f"전체 {total_acc}처 대비 구매 비율 (%)"), use_container_width=True)
    
    st.markdown("#### 3️⃣ 제품별 판매 시즌 집중도 (Seasonality)")
    st.markdown("""<div class="info-box">
    <b>💡 분석 기준:</b> 각 제품의 월 매출을 연간 최대 매출액(1.0) 대비 비율로 환산했습니다. <br>🟥 색이 진할수록 해당 제품의 판매량이 집중되는 <b>성수기</b>입니다.
    </div>""", unsafe_allow_html=True)
    season_pivot = df.pivot_table(index='제품명', columns='월', values='매출액', aggfunc='sum', fill_value=0)
    season_norm = season_pivot.div(season_pivot.max(axis=1), axis=0)
    st.plotly_chart(px.imshow(season_norm, color_continuous_scale="Reds", aspect="auto"), use_container_width=True)

    with st.expander("🧩 **함께 팔기(Cross-selling) 기회 분석기**", expanded=True):
        st.markdown("""<div class="info-box">
        <b>🎯 타겟 추출 기준:</b> Anchor 제품(기존 구매중) 사용처 중, Target 제품(패키지 제안)을 아직 한 번도 구매하지 않은 병원을 선정합니다.
        </div>""", unsafe_allow_html=True)
        col_sel1, col_sel2 = st.columns(2)
        all_prods = sorted(df['제품명'].unique())
        with col_sel1: base_p = st.selectbox("Anchor 제품 (이미 쓰는 것)", all_prods, index=0)
        with col_sel2: target_p = st.selectbox("Target 제품 (팔고 싶은 것)", all_prods, index=min(1, len(all_prods)-1))
        if base_p != target_p:
            acc_A = set(df[df['제품명'] == base_p]['거래처명'].unique())
            acc_B = set(df[df['제품명'] == target_p]['거래처명'].unique())
            targets = list(acc_A - acc_B)
            c_r1, c_r2 = st.columns([1, 2])
            with c_r1: st.metric("🎯 추가 영업 기회", f"{len(targets)} 곳")
            with c_r2:
                if targets:
                    t_info = df[(df['거래처명'].isin(targets)) & (df['제품명'] == base_p)].groupby(['거래처명', '지역'])['매출액'].sum().reset_index().sort_values('매출액', ascending=False)
                    st.dataframe(t_info.head(50), column_config={"매출액": st.column_config.NumberColumn(f"{base_p} 구매액", format="%d 백만원")}, hide_index=True)
                else: st.success("이미 모든 고객이 두 제품을 혼용 중입니다.")

# --------------------------------------------------------------------------------
# 4. 실행 및 필터링
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
df_raw = load_data_from_drive(DRIVE_FILE_ID)
if df_raw.empty: st.stop()

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
        st.markdown("---")
        if st.button("🔗 공유 링크 생성"):
            base_url = "https://skbs-sales-2026-cbktkdtxsyrfzfrihefs2h.streamlit.app/" 
            c_encoded = [urllib.parse.quote(val) for val in sel_channels]
            p_string = f"?y={'&y='.join(map(str, sel_years))}&c={'&c='.join(c_encoded)}&q={'&q='.join(map(str, sel_quarters))}&m={'&m='.join(map(str, sel_months))}"
            st.code(base_url + p_string, language="text")
else:
    sel_cats, sel_products = sorted(df_raw['제품군'].unique()), sorted(df_raw['제품명'].unique())

df_final = df_raw[
    (df_raw['년'].isin(sel_years)) & (df_raw['판매채널'].isin(sel_channels)) &
    (df_raw['분기'].isin(sel_quarters)) & (df_raw['월'].isin(sel_months)) &
    (df_raw['제품군'].isin(sel_cats)) & (df_raw['제품명'].isin(sel_products))
]

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 1. Overview", "🏆 2. VIP & 이탈 관리", "🔄 3. 재유입 패턴 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석"])

with tab1:
    render_smart_overview(df_final, df_raw)
    st.markdown("---")
    st.markdown("### 📈 성과 요약")
    with st.container(border=True):
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
            st.subheader("📅 선택 분기 월별 매출 추이")
            monthly = df_final.groupby('년월')['매출액'].sum().reset_index()
            st.plotly_chart(px.bar(monthly, x='년월', y='매출액', text_auto='.1f', color_discrete_sequence=['#a8dadc']), use_container_width=True)

with tab2:
    st.markdown("### 🏆 VIP 관리 및 거래처 분류 상세 분석")
    st.markdown("""<div class="info-box">
    <b>📊 거래처 상태 분류 기준 상세:</b><br>
    - 🆕 <b>신규:</b> 과거 이력이 전혀 없다가 선택 기간 내 최초 구매한 병원<br>
    - ✅ <b>기존:</b> 전년도와 현재 선택 기간에 연속으로 구매 중인 핵심 병원<br>
    - 🔄 <b>재유입:</b> 전년도는 공백기였으나, 과거 기록이 있고 올해 다시 구매한 복귀 병원<br>
    - 📉 <b>이탈:</b> 과거 구매 이력이 있으나 올해는 현재까지 매출이 없는 병원
    </div>""", unsafe_allow_html=True)
    
    with st.expander("🥇 매출 상위 거래처 (VIP) Top 100", expanded=True):
        st.markdown('<p class="guide-text">💡 아래 표에서 행을 클릭하면 하단에 상세 품목 실적이 표시됩니다.</p>', unsafe_allow_html=True)
        if not df_final.empty:
            ranking = df_final.groupby(['사업자번호', '거래처명', '진료과']).agg({'매출액': 'sum', '수량': 'sum'}).reset_index()
            top100 = ranking.sort_values('매출액', ascending=False).head(100).copy()
            top100['객단가'] = (top100['매출액'] * 1000000 / top100['수량']).round(0)
            cur_date = df_raw['매출일자'].max()
            last_p = df_raw.groupby('사업자번호')['매출일자'].max()
            top100['최근구매일'] = top100['사업자번호'].map(last_p)
            top100['상태'] = top100['최근구매일'].apply(lambda x: '🚨 이탈위험' if (cur_date - x).days >= 90 else '✅ 정상')
            styled_vip = (top100[['상태', '거래처명', '진료과', '매출액', '수량', '객단가']].style.format({'매출액': '{:,.1f}백만원', '객단가': '{:,.0f}원'})
                          .applymap(lambda v: 'color: red; font-weight: bold;' if '이탈' in str(v) else ('color: green; font-weight: bold;' if '정상' in str(v) else ''), subset=['상태']))
            event_vip = st.dataframe(styled_vip, use_container_width=True, on_select="rerun", selection_mode="single-row", height=350)
            if len(event_vip.selection.rows) > 0:
                v_idx = top100.index[event_vip.selection.rows[0]]
                st.subheader(f"🏥 [{top100.loc[v_idx, '거래처명']}] 품목별 실적 합산")
                v_detail = df_raw[df_raw['사업자번호'] == top100.loc[v_idx, '사업자번호']].groupby('제품명').agg({'수량': 'sum', '매출액': 'sum'}).reset_index()
                st.dataframe(v_detail.sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f}백만원'}), use_container_width=True)
    st.markdown("---")
    cls_df = classify_customers(df_raw, sel_years[0])
    c_s1, c_s2 = st.columns([1, 2])
    with c_s1:
        st.markdown('<p class="guide-text">💡 그룹을 선택하면 우측 비중과 하단 리스트가 업데이트됩니다.</p>', unsafe_allow_html=True)
        st.dataframe(cls_df['상태'].value_counts().reset_index().rename(columns={'count':'거래처수'}), use_container_width=True)
        sel_st = st.selectbox("👇 분석할 그룹 선택", sorted(cls_df['상태'].unique()), key="p2_sel")
    with c_s2: st.plotly_chart(px.pie(cls_df[cls_df['상태'] == sel_st], names='진료과', title=f"'{sel_st}' 그룹 진료과 분포"), use_container_width=True)
    event_cls = st.dataframe(cls_df[cls_df['상태'] == sel_st].sort_values('해당년도_매출', ascending=False)[['거래처명', '진료과', '최근구매일', '해당년도_매출']].style.format({'해당년도_매출': '{:,.1f}백만원', '최근구매일': '{:%Y-%m-%d}'}), use_container_width=True, on_select="rerun", selection_mode="single-row")
    if len(event_cls.selection.rows) > 0:
        row_idx = cls_df[cls_df['상태'] == sel_st].sort_values('해당년도_매출', ascending=False).index[event_cls.selection.rows[0]]
        st.markdown("**🏥 상세 구매 이력 (최근 20건)**")
        st.dataframe(df_raw[df_raw['사업자번호'] == row_idx].sort_values('매출일자', ascending=False).head(20)[['매출일자', '제품명', '매출액', '수량']].style.format({'매출액': '{:,.1f}백만원'}), use_container_width=True)

with tab3:
    render_winback_quality(df_raw, sel_years[0])
    st.markdown("---")
    st.markdown("### 🔄 기존 재유입 실적 및 이탈 전 패턴")
    st.markdown('<p class="guide-text">💡 제품명을 클릭하면 해당 제품으로 복귀한 고객의 이탈 전 사용 제품을 확인합니다.</p>', unsafe_allow_html=True)
    df_f = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_f['이전_제품'] = df_f.groupby('사업자번호')['제품명'].shift(1)
    df_f['구매간격'] = (df_f['매출일자'] - df_f.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    res = df_final.merge(df_f[['사업자번호', '매출일자', '이전_제품', '구매간격']], on=['사업자번호', '매출일자'], how='left')
    res = res[res['구매간격'] >= 90]
    if not res.empty:
        st.plotly_chart(px.pie(res, values='매출액', names='제품명', title="재유입 매출 기여 비중"), use_container_width=True)
        res_sum = res.groupby('제품명').agg({'사업자번호': 'nunique', '매출액': 'sum'}).reset_index().rename(columns={'사업자번호': '재유입처수', '매출액': '총_재유입매출'}).sort_values('재유입처수', ascending=False)
        ev_res = st.dataframe(res_sum.style.format({'총_재유입매출': '{:,.1f}백만원'}), use_container_width=True, on_select="rerun", selection_mode="single-row")
        if len(ev_res.selection.rows) > 0:
            s_p = res_sum.iloc[ev_res.selection.rows[0]]['제품명']
            st.plotly_chart(px.bar(res[res['제품명'] == s_p].groupby('이전_제품').size().reset_index(name='count').sort_values('count', ascending=False).head(10), x='count', y='이전_제품', orientation='h', title=f"[{s_p}]로 복귀한 고객들이 과거에 썼던 제품"), use_container_width=True)
    else: st.info("선택한 조건에서 재유입 데이터가 없습니다.")

with tab4:
    render_regional_deep_dive(df_final)
    st.markdown("---")
    st.markdown("### 🗺️ 지역별 실적 및 거점 리스트")
    reg_s = df_final.groupby('지역').agg({'매출액': 'sum', '사업자번호': 'nunique'}).reset_index().rename(columns={'사업자번호': '구매처수'}).sort_values('매출액', ascending=False)
    c_r1, c_r2 = st.columns([1, 1.5])
    with c_r1:
        st.markdown('<p class="guide-text">💡 지역을 선택하면 우측 비중과 하단 병원 리스트가 연동됩니다.</p>', unsafe_allow_html=True)
        st.dataframe(reg_s[['지역', '매출액', '구매처수']].style.format({'매출액': '{:,.0f}백만원'}), use_container_width=True)
        sel_reg = st.selectbox("🔎 분석할 지역 선택", reg_s['지역'].unique(), key="p4_reg_orig")
    with c_r2:
        if sel_reg:
            st.plotly_chart(px.pie(df_final[df_final['지역'] == sel_reg], values='매출액', names='제품명', hole=0.3, title=f"[{sel_reg}] 제품 비중"), use_container_width=True)
    if sel_reg:
        st.markdown(f"**🏠 [{sel_reg}] 상위 매출 거래처 리스트**")
        r_agg = df_final[df_final['지역'] == sel_reg].groupby(['거래처명', '제품명']).agg({'매출액': 'sum', '수량': 'sum'}).reset_index().sort_values('매출액', ascending=False).head(50)
        st.dataframe(r_agg.style.format({'매출액': '{:,.1f}백만원'}), use_container_width=True)

with tab5:
    render_product_strategy(df_final)
    st.markdown("---")
    st.markdown("### 📦 제품별 판매 현황 및 고객 상세 분석")
    st.markdown('<p class="guide-text">💡 아래 리스트에서 제품을 클릭하면 해당 제품을 구매한 병원 상세 리스트가 표시됩니다.</p>', unsafe_allow_html=True)
    p_main = df_final.groupby('제품명').agg({'수량': 'sum', '매출액': 'sum', '사업자번호': 'nunique'}).reset_index().rename(columns={'사업자번호': '구매처수'}).sort_values('매출액', ascending=False)
    ev_p = st.dataframe(p_main.style.format({'매출액': '{:,.1f}백만원', '수량': '{:,.0f}'}), use_container_width=True, on_select="rerun", selection_mode="single-row", height=300)
    if len(ev_p.selection.rows) > 0:
        sel_p_name = p_main.iloc[ev_p.selection.rows[0]]['제품명']
        st.subheader(f"🎯 [{sel_p_name}] 구매 거래처 리스트")
        p_detail = df_final[df_final['제품명'] == sel_p_name].groupby('거래처명').agg({'수량': 'sum', '매출액': 'sum'}).reset_index()
        st.dataframe(p_detail.sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f}백만원'}), use_container_width=True)
