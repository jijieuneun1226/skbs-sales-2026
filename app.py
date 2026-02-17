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
# 2. 데이터 로드 및 전처리 (멀티 시트 로드 로직 포함)
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
        
        # 메인 시트 'SKBS' 로드
        df = pd.read_excel(xls, sheet_name='SKBS' if 'SKBS' in sheets else sheets[0])
        
        # 브랜드 분석 데이터 로드
        for sn in ['Brand_Monthly', 'Brand_Total', 'Brand_Direct_Sales']:
            if sn in sheets:
                b_df = pd.read_excel(xls, sheet_name=sn)
                b_df.columns = [re.sub(r'\s+', '', str(c)) for c in b_df.columns]
                brand_data[sn] = b_df
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
        if '지역' not in df.columns and '주소' in df.columns:
            df['지역'] = df['주소'].astype(str).str.split().str[0]
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
    except Exception as e:
        st.error(f"❌ 전처리 오류: {e}"); return pd.DataFrame(), {}
    return df, brand_data

# --------------------------------------------------------------------------------
# 3. [SK분석 기본 폼] 분석 함수 정의 (사용자 원본 1~5 탭 100% 동일)
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
    st.markdown("""<div class="info-box">
    <b>💡 분석 지표 기준:</b> 신규(전년무→올해유), 이탈(전년유→올해무), 유지율(전년유→올해유 비율)
    </div>""", unsafe_allow_html=True)
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
    st.markdown("""<div class="info-box">
    <b>🔍 재유입 정의:</b> 직전 구매일로부터 <b>최소 180일(6개월) 이상 공백기</b> 이후 다시 구매가 발생한 거래처 (선택 기간 내 첫 구매 기준)<br>
    <b>🚦 회복 퀄리티:</b> 과거 전성기(최고 매출) 대비 현재 매출 수준<br>
    - 🟢 <b>완전 회복:</b> 80% 이상 / 🟡 <b>회복 중:</b> 20~80% / 🔴 <b>재진입 초기:</b> 20% 미만<br>
    <b>📈 평균 회복률 뜻:</b> 재유입된 거래처들이 과거 가장 많이 구매했던 시기 대비 현재 평균적으로 몇 %까지 구매력이 회복되었는지를 나타냄
    </div>""", unsafe_allow_html=True)

    df_history = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_history['구매간격'] = (df_history['매출일자'] - df_history.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    winback_data = df_history[(df_history['사업자번호'].isin(df_final['사업자번호'])) & (df_history['구매간격'] >= 180)].copy()
    winback_ids = winback_data['사업자번호'].unique()
    
    if len(winback_ids) == 0:
        st.info("♻️ 해당 조건 내 재유입 데이터(6개월 공백 기준)가 없습니다."); return

    sales_curr = df_final[df_final['사업자번호'].isin(winback_ids)].groupby(['사업자번호', '거래처명', '지역'])['매출액'].sum()
    sales_history = df_raw[df_raw['사업자번호'].isin(winback_ids)].groupby(['사업자번호', '거래처명', '지역'])['매출액'].max()
    
    df_wb = pd.DataFrame(index=sales_curr.index)
    df_wb['올해매출'] = sales_curr
    df_wb['과거최고'] = sales_history
    df_wb['회복률'] = (df_wb['올해매출'] / df_wb['과거최고'].replace(0,1) * 100).fillna(0)
    df_wb['상태'] = df_wb['회복률'].apply(lambda x: "완전 회복" if x>=80 else ("회복 중" if x>=20 else "재진입 초기"))
    df_wb = df_wb.reset_index().sort_values('올해매출', ascending=False)
    df_wb['Bubble_Size'] = df_wb['올해매출'].apply(lambda x: max(x, 0.1))

    c1, c2, c3 = st.columns(3)
    c1.metric("재유입 거래처", f"{len(df_wb)} 처")
    c2.metric("확보 매출", f"{df_wb['올해매출'].sum():,.0f} 백만원")
    c3.metric("평균 회복률", f"{df_wb['회복률'].mean():.1f}%")
    
    col_ch, col_li = st.columns([1, 1])
    with col_ch:
        try:
            fig = px.scatter(df_wb, x='과거최고', y='올해매출', color='상태', hover_name='거래처명', size='Bubble_Size',
                             category_orders={"상태": ["완전 회복", "회복 중", "재진입 초기"]},
                             color_discrete_map={"완전 회복": "green", "회복 중": "orange", "재진입 초기": "red"})
            fig.add_shape(type="line", x0=0, y0=0, x1=df_wb['과거최고'].max(), y1=df_wb['과거최고'].max(), line=dict(color="gray", dash="dash"))
            st.plotly_chart(fig, use_container_width=True)
        except: st.warning("차트 생성 불가")
    with col_li:
        st.markdown('<p class="guide-text">💡 리스트의 행을 클릭하면 하단에서 실제 공백 기간과 구매 이력을 확인할 수 있습니다.</p>', unsafe_allow_html=True)
        event_wb = st.dataframe(df_wb[['상태', '거래처명', '올해매출', '회복률']], hide_index=True, use_container_width=True,
                               on_select="rerun", selection_mode="single-row",
                               column_config={"회복률": st.column_config.ProgressColumn("회복도", format="%.1f%%", min_value=0, max_value=100), "올해매출": st.column_config.NumberColumn(format="%.1f 백만원")})

    if len(event_wb.selection.rows) > 0:
        sel_idx = event_wb.selection.rows[0]
        sel_biz_no = df_wb.iloc[sel_idx]['사업자번호']
        sel_name = df_wb.iloc[sel_idx]['거래처명']
        st.markdown(f"#### 🔍 [{sel_name}] 실제 구매 간격 및 상세 내역")
        detail_hist = df_history[df_history['사업자번호'] == sel_biz_no].sort_values('매출일자', ascending=False).copy()
        detail_hist['매출일자_str'] = detail_hist['매출일자'].dt.strftime('%Y-%m-%d')
        st.dataframe(detail_hist[['매출일자_str', '제품명', '매출액', '수량', '구매간격']].rename(columns={'매출일자_str':'매출일자', '구매간격':'직전구매후공백(일)'})
                     .style.applymap(lambda v: 'background-color: #ffcccc; font-weight: bold;' if isinstance(v, (int, float)) and v >= 180 else '', subset=['직전구매후공백(일)'])
                     .format({'매출액': '{:,.1f} 백만원', '직전구매후공백(일)': '{:,.0f} 일'}), 
                     use_container_width=True)

def render_regional_deep_dive(df):
    if df.empty: return
    reg_stats = df.groupby('지역').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    reg_stats['Per'] = reg_stats['Sales'] / reg_stats['Count']
    
    st.markdown("### 🗺️ 지역별 심층 효율성 및 거점 영향력 분석")
    st.markdown(f"""<div class="info-box">
    <b>📈 지역 전략 요약:</b><br>
    - <b>최고 매출 지역:</b> 기간 내 전체 합산 매출액이 가장 큰 지역<br>
    - <b>영업 효율 1위:</b> 거래처 1처당 평균 매출(객단가)이 가장 높은 지역<br>
    - <b>활성 지역 수:</b> 기간 내 단 1건이라도 매출이 발생한 총 행정 구역 수<br>
    - <b>핵심 거점 의존도:</b> 지역 내 1위 거래처가 차지하는 매출 비중. 높을수록 해당 거래처 이탈 시 리스크가 큼
    </div>""", unsafe_allow_html=True)

    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.metric("최고 매출 지역", reg_stats.loc[reg_stats['Sales'].idxmax(), '지역'])
        c2.metric("영업 효율 1위", reg_stats.loc[reg_stats['Per'].idxmax(), '지역'], f"{reg_stats['Per'].max():.1f} 백만원/처")
        c3.metric("활성 지역 수", f"{len(reg_stats)} 개")

    fig = px.scatter(reg_stats, x='Count', y='Per', size='Sales', color='지역', text='지역', 
                     labels={'Count': '거래처 수', 'Per': '평균 객단가 (백만원)'})
    fig.add_hline(y=reg_stats['Per'].mean(), line_dash="dash", line_color="gray")
    st.plotly_chart(fig, use_container_width=True)
    
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(px.pie(reg_stats, values='Sales', names='지역', hole=0.3, title="지역별 매출 기여도 (%)"), use_container_width=True)
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
    with c1:
        st.plotly_chart(px.scatter(p_stats, x='Count', y='Sales', size='Bubble_Size', color='제품명', text='제품명', title="제품 BCG 매트릭스 (병원수 vs 매출)"), use_container_width=True)
    with c2:
        total_acc = df['사업자번호'].nunique()
        p_stats['Penetration'] = (p_stats['Count'] / total_acc) * 100
        st.plotly_chart(px.bar(p_stats.sort_values('Penetration'), x='Penetration', y='제품명', orientation='h', text_auto='.1f', title=f"시장 침투율 (%)"), use_container_width=True)
    
    st.markdown("#### 📅 제품별 판매 시즌 집중도 (Seasonality)")
    st.markdown("""<div class="info-box">
    <b>💡 분석 가이드:</b> 연간 최대 매출 월을 1.0으로 환산. 🟥 진할수록 성수기임을 의미합니다.
    </div>""", unsafe_allow_html=True)
    season_pivot = df.pivot_table(index='제품명', columns='월', values='매출액', aggfunc='sum', fill_value=0)
    st.plotly_chart(px.imshow(season_pivot.div(season_pivot.max(axis=1), axis=0), color_continuous_scale="Reds", aspect="auto"), use_container_width=True)

    with st.expander("🧩 **함께 팔기(Cross-selling) 기회 분석기**", expanded=True):
        st.markdown("""<div class="info-box">
        <b>🎯 추출 기준:</b> Anchor 제품(기존 사용중) 구매처 중, Target 제품(패키지 제안)을 아직 한 번도 구매하지 않은 병원 리스트를 추출합니다.
        </div>""", unsafe_allow_html=True)
        col_sel1, col_sel2 = st.columns(2)
        all_prods = sorted(df['제품명'].unique())
        with col_sel1: base_p = st.selectbox("Anchor 제품 (이미 쓰는 것)", all_prods, index=0)
        with col_sel2: target_p = st.selectbox("Target 제품 (팔고 싶은 것)", all_prods, index=min(1, len(all_prods)-1))
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
    classification = {}
    for biz_no in base_info.index:
        has_ty = (target_year in cust_year.columns) and (cust_year.loc[biz_no, target_year] > 0)
        has_t1 = (target_year - 1 in cust_year.columns) and (cust_year.loc[biz_no, target_year - 1] > 0)
        has_t2 = (target_year - 2 in cust_year.columns) and (cust_year.loc[biz_no, target_year - 2] > 0)
        has_t3 = (target_year - 3 in cust_year.columns) and (cust_year.loc[biz_no, target_year - 3] > 0)
        past_y = [y for y in cust_year.columns if y < target_year - 1]
        has_h = cust_year.loc[biz_no, past_y].sum() > 0 if past_y else False
        if has_ty:
            if has_t1: status = "✅ 기존 (유지)"
            else: status = "🔄 재유입 (복귀)" if has_h else "🆕 신규 (New)"
        else:
            if has_t1: status = "📉 1년 이탈"
            elif has_t2: status = "📉 2년 이탈"
            elif has_t3: status = "📉 3년 이탈"
            else: status = "💤 장기 이탈"
        classification[biz_no] = status
    base_info['상태'] = base_info.index.map(classification)
    return base_info

# --------------------------------------------------------------------------------
# [추가 및 보완] 🏠 6. 브랜드관 성과 분석 (요청 디자인 및 로직 100% 반영)
# --------------------------------------------------------------------------------
def render_brand_store_analysis(brand_data, sel_years, df_raw):
    st.markdown("### 🏠 브랜드관 성과 및 마케팅 효용성 분석")
    target_year = sel_years[0]
    
    if not brand_data or brand_data['Brand_Total'].empty:
        st.warning("⚠️ 브랜드관 분석 시트가 존재하지 않습니다."); return

    # 1. 제품명 매핑 (브랜드관 품목명 -> 세일즈 로우 데이터 제품명 치환)
    valid_products = df_raw['제품명'].unique()
    def map_skbs_product(name):
        clean = str(name).replace('[SK]', '').replace('주', '').replace('0.5ml', '').replace('/ 1V', '').replace('x 1PFS', '').strip()
        for vp in valid_products:
            if clean[:5] in vp: return vp
        return str(name).strip()

    # 2. 데이터 필터링 (선택 년도 기준)
    df_total = brand_data['Brand_Total'][brand_data['Brand_Total']['년도'] == target_year]
    df_direct = brand_data['Brand_Direct_Sales'].copy()
    if not df_direct.empty:
        df_direct['구매일'] = pd.to_datetime(df_direct['구매일'])
        df_direct = df_direct[df_direct['구매일'].dt.year == target_year]
        df_direct['상품명_매핑'] = df_direct['상품명'].apply(map_skbs_product)
        df_direct['매출_백만'] = df_direct['매출'] / 1000000

    df_monthly = brand_data['Brand_Monthly'].copy()
    if not df_monthly.empty:
        df_monthly = df_monthly[df_monthly['월'].str.startswith(str(target_year))]

    # 3. 주요 지표 계산 (백만원 단위)
    uv = df_total['UV'].sum() if not df_total.empty else 0
    pv = df_total['PV'].sum() if not df_total.empty else 0
    conv_sales_mil = df_direct['매출_백만'].sum() if not df_direct.empty else 0
    conv_count = df_direct['사업자번호'].nunique() if not df_direct.empty else 0
    atv_won = (conv_sales_mil * 1000000 / conv_count) if conv_count > 0 else 0

    # 4. 상단 Summary (1번 탭 디자인 이식)
    st.markdown(f"### 🚀 브랜드관 성과 Summary ({target_year}년)")
    st.markdown("""<div class="info-box"><b>💡 분석 지표 기준:</b> 브랜드관 페이지 유입 회원의 활동성과 실제 구매 전환(당일 구매)을 분석합니다.</div>""", unsafe_allow_html=True)
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        with c1:
            st.metric("💰 브랜드관 전환 매출", f"{conv_sales_mil:,.1f} 백만원")
            if not df_monthly.empty: st.area_chart(df_monthly.set_index('월')['UV'], height=50, color="#4e79a7")
        with c2:
            st.metric("👥 유입 고객 (UV)", f"{uv:,} 명")
            if uv > 0: st.progress(conv_count / uv, text=f"전환율 {(conv_count/uv*100):.1f}%")
        with c3:
            st.metric("💳 평균 객단가 (ATV)", f"{atv_won:,.0f} 원")
            st.write(f"누적 페이지뷰: **{pv:,} PV**")

    # 5. 브랜드관 운영 총괄 성과 표 (테이블 디자인)
    st.markdown("#### 📊 브랜드관 운영 총괄 성과")
    summary_df = pd.DataFrame({
        "구분": ["UV (방문자수)", "브랜드관 전환 매출액", "구매 전환 처수", "객단가 (ATV)"],
        "성과 지표": [f"{uv:,} 명", f"{conv_sales_mil:,.1f} 백만원", f"{conv_count:,} 처", f"{atv_won:,.0f} 원"]
    })
    st.table(summary_df)

    # 6. 월별 추이 및 진료과 비중
    st.markdown("---")
    col_chart, col_dept = st.columns([1.5, 1])
    with col_chart:
        st.markdown("#### 📅 월별 유입 및 관심도 추이")
        if not df_monthly.empty:
            fig_l = px.line(df_monthly, x='월', y=['UV', 'PV'], markers=True, color_discrete_sequence=['#4e79a7', '#e15759'])
            st.plotly_chart(fig_l, use_container_width=True)
    with col_dept:
        st.markdown("#### 🏥 전환 고객 진료과 비중")
        if not df_direct.empty:
            st.plotly_chart(px.pie(df_direct, values='매출', names='진료과', hole=0.4), use_container_width=True)

    # 7. 매출 기여도 상위 5
    st.markdown("#### 🛍️ 브랜드관 구매 전환 매출 기여도 (Top 5)")
    if not df_direct.empty:
        top5 = df_direct.groupby('상품명_매핑').agg(매출액=('매출_백만', 'sum'), 구매수량=('수량', 'sum')).sort_values('매출액', ascending=False).head(5)
        st.dataframe(top5.style.format({'매출액': '{:,.1f} 백만원', '구매수량': '{:,} 개'}), use_container_width=True)

# --------------------------------------------------------------------------------
# 4. 필터 및 메인 실행 (Tab 1~6 전체 보존)
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
        st.markdown("---")
        if st.button("🔗 뷰어용 공유 링크 생성"):
            base_url = "https://skbs-sales-2026-cbktkdtxsyrfzfrihefs2h.streamlit.app/" 
            cat_p = "all" if len(sel_cats) == len(df_raw['제품군'].unique()) else "&cat=".join([urllib.parse.quote(val) for val in sel_cats])
            prod_p = "all" if len(sel_products) == len(df_raw['제품명'].unique()) else "&prod=".join([urllib.parse.quote(val) for val in sel_products])
            p_string = (f"?y={'&y='.join(map(str, sel_years))}&c={'&c='.join(sel_channels)}&q={'&q='.join(map(str, sel_quarters))}"
                        f"&m={'&m='.join(map(str, sel_months))}&cat={cat_p}&prod={prod_p}")
            st.success("공유 링크가 생성되었습니다!"); st.code(base_url + p_string, language="text")

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
        st.markdown("---")
        col_a, col_b = st.columns([1, 1.5])
        with col_a: st.plotly_chart(px.pie(df_final, values='매출액', names='진료과', hole=0.4, title="진료과별 매출 비중"), use_container_width=True)
        with col_b:
            monthly_b = df_final.groupby('년월')['매출액'].sum().reset_index()
            st.plotly_chart(px.bar(monthly_b, x='년월', y='매출액', text_auto='.1f', title="월별 매출 추이", color_discrete_sequence=['#a8dadc']), use_container_width=True)

with tab2:
    if not df_final.empty:
        total_s = df_final['매출액'].sum()
        ranking_v = df_final.groupby(['사업자번호', '거래처명', '진료과']).agg({'매출액': 'sum'}).sort_values('매출액', ascending=False).head(100).reset_index()
        cls_d = classify_customers(df_raw, sel_years[0])
        st_c = cls_d['상태'].value_counts()
        last_p = df_raw.groupby('사업자번호')['매출일자'].max()
        ranking_v['최근구매일'] = ranking_v['사업자번호'].map(last_p)
        cur_d = df_raw['매출일자'].max()
        risk_cnt = len(ranking_v[(cur_d - ranking_v['최근구매일']).dt.days >= 90])
        top_v = ranking_v.iloc[0]
        dept_sum = df_final.groupby('진료과')['매출액'].sum().sort_values(ascending=False)

        st.subheader("✔️ Summary")
        st.write(f"• 상위 100처 매출 합계: **{ranking_v['매출액'].sum()/100:,.1f}억원** (전체 매출의 {(ranking_v['매출액'].sum()/total_s*100):.1f}% 차지)")
        st.write(f"• 거래처 상태 분포: **기존({st_c.get('✅ 기존 (유지)',0)}처), 신규({st_c.get('🆕 신규 (New)',0)}처), 재유입({st_c.get('🔄 재유입 (복귀)',0)}처), 이탈({st_c.get('📉 1년 이탈',0)}처)**")
        st.write(f"• 최고 매출 거래처: **{top_v['거래처명']}** ({top_v['매출액']:,.1f} 백만원)")
        st.subheader("✔️ Insight")
        st.write(f"• **VIP 이탈 위험 알림:** 현재 상위 100처 거래처 중 **{risk_cnt}처({risk_cnt}%)**가 90일 이상 구매가 없는 이탈 위험 상태입니다. 특히 **{top_v['거래처명']}**의 관리가 시급합니다.")

    st.markdown("---")
    st.markdown("### 🏆 상위 거래처 및 거래처 분류 상세 분석")
    with st.expander("🥇 매출 상위 거래처 Top 100", expanded=True):
        ranking_v['최근구매일_str'] = ranking_v['최근구매일'].dt.strftime('%Y-%m-%d')
        ranking_v['상태'] = (cur_d - ranking_v['최근구매일']).dt.days.apply(lambda x: '🚨 이탈위험' if x >= 90 else '✅ 정상')
        event_vip = st.dataframe(ranking_v[['상태', '거래처명', '진료과', '매출액', '최근구매일_str']], use_container_width=True, on_select="rerun", selection_mode="single-row", height=350)
        if len(event_vip.selection.rows) > 0:
            v_idx = ranking_v.index[event_vip.selection.rows[0]]
            st.dataframe(df_raw[df_raw['사업자번호'] == ranking_v.loc[v_idx, '사업자번호']].groupby('제품명').agg({'매출액': 'sum'}).sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True)
    st.markdown("---")
    c_s1, c_s2 = st.columns([1, 2])
    with c_s1:
        st.dataframe(cls_d['상태'].value_counts().reset_index().rename(columns={'count':'거래처수'}), use_container_width=True)
        sel_st = st.selectbox("👇 분석할 그룹 선택", sorted(cls_d['상태'].unique()), key="p2_sel")
    with c_s2: st.plotly_chart(px.pie(cls_d[cls_d['상태'] == sel_st], names='진료과', title="진료과 분포"), use_container_width=True)
    display_cls = cls_d[cls_d['상태'] == sel_st].sort_values('해당년도_매출', ascending=False).copy()
    display_cls['최근구매일'] = display_cls['최근구매일'].dt.strftime('%Y-%m-%d')
    event_cls = st.dataframe(display_cls[['거래처명', '진료과', '최근구매일', '해당년도_매출']], use_container_width=True, on_select="rerun", selection_mode="single-row")
    if len(event_cls.selection.rows) > 0:
        row_biz = display_cls.index[event_cls.selection.rows[0]]
        h_df = df_raw[df_raw['사업자번호'] == row_biz].sort_values('매출일자', ascending=False).head(20).copy()
        h_df['매출일자'] = h_df['매출일자'].dt.strftime('%Y-%m-%d')
        st.dataframe(h_df[['매출일자', '제품명', '매출액', '수량']].style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True)

with tab3: render_winback_quality(df_final, df_raw, sel_years[0])
with tab4: render_regional_deep_dive(df_final)
with tab5:
    p_stats_v5 = df_final.groupby('제품명').agg(Sales=('매출액', 'sum'), Qty=('수량', 'sum'), Count=('사업자번호', 'nunique')).reset_index().sort_values('Sales', ascending=False)
    st.markdown("### 📦 제품별 판매 현황")
    c_p1, c_p2 = st.columns(2)
    with c_p1: st.plotly_chart(px.bar(p_stats_v5, x='Sales', y='제품명', orientation='h', title="제품별 매출 현황", color='Sales'), use_container_width=True)
    with c_p2: st.plotly_chart(px.pie(df_final.groupby('제품군')['매출액'].sum().reset_index(), values='매출액', names='제품군', hole=0.3, title="제품군별 매출 비중"), use_container_width=True)
    render_product_strategy(df_final)
    st.markdown("### 📦 제품별 판매 현황 리스트")
    p_main_v = df_final.groupby('제품명').agg({'수량': 'sum', '매출액': 'sum', '사업자번호': 'nunique'}).reset_index().sort_values('매출액', ascending=False)
    ev_p_v = st.dataframe(p_main_v.rename(columns={'사업자번호':'구매처수'}).style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True, on_select="rerun", selection_mode="single-row", height=300)
    if len(ev_p_v.selection.rows) > 0:
        sel_p_v = p_main_v.iloc[ev_p_v.selection.rows[0]]['제품명']
        st.dataframe(df_final[df_final['제품명'] == sel_p_v].groupby('거래처명').agg({'매출액': 'sum'}).reset_index().sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True)

with tab6:
    render_brand_store_analysis(brand_data_dict, sel_years, df_raw)
