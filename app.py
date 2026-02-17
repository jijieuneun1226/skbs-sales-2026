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

def get_p(key, default):
    res = params.get_all(key)
    if not res: return default
    if key in ['y', 'q', 'm']: return [int(x) for x in res]
    return res

# --------------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600, max_entries=2)
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
        
        if response.status_code != 200: return pd.DataFrame()
        file_bytes = io.BytesIO(response.content)
        df = pd.read_excel(file_bytes, engine='openpyxl')
    except Exception as e:
        st.error(f"❌ 로드 오류: {e}"); return pd.DataFrame()

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
# 4. 필터 및 실행
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
df_raw = load_data_from_drive(DRIVE_FILE_ID)
if df_raw.empty: st.stop()

sel_years = get_p('y', [df_raw['년'].max()])
sel_channels = get_p('c', sorted(df_raw['판매채널'].unique()))
sel_quarters = get_p('q', sorted(df_raw['분기'].unique()))
sel_months = get_p('m', sorted(df_raw['월'].unique()))
sel_cats = get_p('cat', sorted(df_raw['제품군'].unique()))
sel_products = get_p('prod', sorted(df_raw['제품명'].unique()))

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
            c_encoded = [urllib.parse.quote(val) for val in sel_channels]
            cat_encoded = [urllib.parse.quote(val) for val in sel_cats]
            prod_encoded = [urllib.parse.quote(val) for val in sel_products]
            p_string = (
                f"?y={'&y='.join(map(str, sel_years))}"
                f"&c={'&c='.join(c_encoded)}"
                f"&q={'&q='.join(map(str, sel_quarters))}"
                f"&m={'&m='.join(map(str, sel_months))}"
                f"&cat={'&cat='.join(cat_encoded)}"
                f"&prod={'&prod='.join(prod_encoded)}"
            )
            st.success("공유 링크가 생성되었습니다!")
            st.code(base_url + p_string, language="text")

df_final = df_raw[
    (df_raw['년'].isin(sel_years)) & 
    (df_raw['판매채널'].isin(sel_channels)) &
    (df_raw['분기'].isin(sel_quarters)) & 
    (df_raw['월'].isin(sel_months)) &
    (df_raw['제품군'].isin(sel_cats)) &
    (df_raw['제품명'].isin(sel_products))
]

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 1. Overview", "🏆 2. 상위 거래처 & 이탈 관리", "🔄 3. 재유입 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석"])

with tab1:
    current_year = int(df_final['년'].max()) if not df_final.empty else 2026
    last_year = current_year - 1
    df_prev = df_raw[(df_raw['년'] == last_year) & (df_raw['월'].isin(df_final['월'].unique()))]
    sales_curr, sales_prev = df_final['매출액'].sum(), df_prev['매출액'].sum()
    sales_pct = ((sales_curr - sales_prev) / (sales_prev if sales_prev > 0 else 1) * 100)
    cust_curr, cust_prev = set(df_final['사업자번호']), set(df_prev['사업자번호'])
    new_cust, lost_cust, retained_cust = len(cust_curr - cust_prev), len(cust_prev - cust_curr), len(cust_curr & cust_prev)

    st.markdown(f"### 🚀 {current_year}년 Executive Summary (vs {last_year})")
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        with c1:
            st.metric("💰 총 매출 실적", f"{sales_curr:,.0f} 백만원", f"{sales_pct:+.1f}% (YoY)")
            st.area_chart(df_final.groupby('월')['매출액'].sum(), height=50, color="#FF4B4B")
        with c2:
            st.metric("🏥 총 거래 병원", f"{len(cust_curr)} 처")
            st.markdown(f"- ✨신규: <span style='color:blue'>+{new_cust}</span> / 💔이탈: <span style='color:red'>-{lost_cust}</span>", unsafe_allow_html=True)
            if len(cust_curr) > 0: st.progress(retained_cust / len(cust_curr), text=f"고객 유지율 {(retained_cust/len(cust_curr))*100:.1f}%")
        with c3:
            top_p = df_final.groupby('제품명')['매출액'].sum().idxmax() if not df_final.empty else "N/A"
            st.metric("🏆 Best Product", top_p)
            st.write(f"기여: **{df_final.groupby('제품명')['매출액'].sum().max():,.0f} 백만원**")
    
    st.markdown("---")
    with st.container(border=True):
        st.markdown("### 📈 년도/분기 현황 요약")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 매출액 (년도)", f"{df_raw[df_raw['년'].isin(sel_years)]['매출액'].sum():,.0f} 백만원")
        c2.metric("총 구매처수 (년도)", f"{df_raw[df_raw['년'].isin(sel_years)]['사업자번호'].nunique():,} 처")
        c3.metric("선택기간 매출액", f"{df_final['매출액'].sum():,.0f} 백만원")
        c4.metric("선택기간 구매처수", f"{df_final['사업자번호'].nunique():,} 처")
        st.markdown("---")
        col_a, col_b = st.columns([1, 1.5])
        with col_a: st.plotly_chart(px.pie(df_final, values='매출액', names='진료과', hole=0.4, title="진료과별 매출 비중"), use_container_width=True)
        with col_b:
            monthly_b = df_final.groupby('년월')['매출액'].sum().reset_index()
            st.plotly_chart(px.bar(monthly_b, x='년월', y='매출액', text_auto='.1f', title="월별 매출 추이", color_discrete_sequence=['#a8dadc']), use_container_width=True)

with tab2:
    st.markdown("### 🏆 상위 거래처 및 거래처 분류 상세 분석")
    
    # 탭 2 요약 데이터
    if not df_final.empty:
        ranking = df_final.groupby(['사업자번호', '거래처명', '진료과']).agg({'매출액': 'sum', '수량': 'sum'}).reset_index()
        top100 = ranking.sort_values('매출액', ascending=False).head(100).copy()
        total_sales = df_final['매출액'].sum()
        top100_sum = top100['매출액'].sum()
        top100_ratio = (top100_sum / total_sales * 100)
        
        cur_date = df_raw['매출일자'].max()
        last_p = df_raw.groupby('사업자번호')['매출일자'].max()
        top100['최근구매일_dt'] = top100['사업자번호'].map(last_p)
        top100['공백일수'] = (cur_date - top100['최근구매일_dt']).dt.days
        risk_cnt = len(top100[top100['공백일수'] >= 90])
        
        cls_df = classify_customers(df_raw, sel_years[0])
        status_counts = cls_df['상태'].value_counts()

        st.markdown(f"""
        **[📊 데이터 요약]**
        * 상위 100대 매출 합계: **{top100_sum:,.0f} 백만원** (전체 매출의 **{top100_ratio:.1f}%** 차지)
        * 거래처 상태 분포: **기존({status_counts.get('✅ 기존 (유지)',0)}처), 신규({status_counts.get('🆕 신규 (New)',0)}처), 재유입({status_counts.get('🔄 재유입 (복귀)',0)}처), 이탈({status_counts.get('📉 1년 이탈',0)}처)**
        * 평균 객단가: 처당 약 **{ (top100_sum / 100 * 100):,.0f} 만원**
        * 최고 매출 거래처: **{top100.iloc[0]['거래처명']}** ({top100.iloc[0]['매출액']:,.0f} 백만원)
        """)

        # 탭 2 스마트 인사이트
        insight_msg = f"현재 상위 100대 거래처 중 **{risk_cnt}처({risk_cnt}%)**가 90일 이상 구매가 없는 **이탈 위험** 상태입니다. 특히 매출 기여도가 가장 높은 **{top100.iloc[0]['거래처명']}**의 공백이 지속되고 있어 즉각적인 관리가 필요합니다."
        if risk_cnt > 30: st.error(insight_msg)
        elif risk_cnt > 10: st.warning(insight_msg)
        else: st.info(insight_msg)

    st.markdown("""<div class="info-box">
    🆕 <b>신규:</b> 최초구매 / ✅ <b>기존:</b> 연속구매 / 🔄 <b>재유입:</b> 공백 후 복귀 / 📉 <b>이탈:</b> 기간 내 구매 부재<br>
    ※ <b>VIP 상태 정의:</b> 최근 구매일로부터 90일 이내면 <b>'✅ 정상'</b>, 90일 초과 시 <b>'🚨 이탈위험'</b>으로 분류
    </div>""", unsafe_allow_html=True)
    
    with st.expander("🥇 매출 상위 거래처 Top 100", expanded=True):
        st.markdown('<p class="guide-text">💡 아래 표에서 행을 클릭하면 하단에 상세 실적이 표시됩니다.</p>', unsafe_allow_html=True)
        if not df_final.empty:
            top100['최근구매일'] = top100['최근구매일_dt'].dt.strftime('%Y-%m-%d')
            top100['상태'] = top100['공백일수'].apply(lambda x: '🚨 이탈위험' if x >= 90 else '✅ 정상')
            styled_vip = (top100[['상태', '거래처명', '진료과', '매출액', '최근구매일']].style.format({'매출액': '{:,.1f} 백만원'})
                          .applymap(lambda v: 'color: red; font-weight: bold;' if '이탈' in str(v) else ('color: green;' if '정상' in str(v) else ''), subset=['상태']))
            event_vip = st.dataframe(styled_vip, use_container_width=True, on_select="rerun", selection_mode="single-row", height=350)
            if len(event_vip.selection.rows) > 0:
                v_idx = top100.index[event_vip.selection.rows[0]]
                v_detail = df_raw[df_raw['사업자번호'] == top100.loc[v_idx, '사업자번호']].groupby('제품명').agg({'매출액': 'sum'}).reset_index()
                st.dataframe(v_detail.sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True)
    
    st.markdown("---")
    c_s1, c_s2 = st.columns([1, 2])
    with c_s1:
        st.markdown('<p class="guide-text">💡 그룹 선택 시 하단 리스트가 연동됩니다.</p>', unsafe_allow_html=True)
        st.dataframe(cls_df['상태'].value_counts().reset_index().rename(columns={'count':'거래처수'}), use_container_width=True)
        sel_st = st.selectbox("👇 분석할 그룹 선택", sorted(cls_df['상태'].unique()), key="p2_sel")
    with c_s2: st.plotly_chart(px.pie(cls_df[cls_df['상태'] == sel_st], names='진료과', title="진료과 분포"), use_container_width=True)
    
    display_cls = cls_df[cls_df['상태'] == sel_st].sort_values('해당년도_매출', ascending=False).copy()
    display_cls['최근구매일'] = display_cls['최근구매일'].dt.strftime('%Y-%m-%d')
    event_cls = st.dataframe(display_cls[['거래처명', '진료과', '최근구매일', '해당년도_매출']].style.format({'해당년도_매출': '{:,.1f} 백만원'}), use_container_width=True, on_select="rerun", selection_mode="single-row")
    if len(event_cls.selection.rows) > 0:
        row_idx = display_cls.index[event_cls.selection.rows[0]]
        st.markdown("**🏥 상세 구매 이력 (최근 20건)**")
        hist_df = df_raw[df_raw['사업자번호'] == row_idx].sort_values('매출일자', ascending=False).head(20).copy()
        hist_df['매출일자'] = hist_df['매출일자'].dt.strftime('%Y-%m-%d')
        st.dataframe(hist_df[['매출일자', '제품명', '매출액', '수량']].style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True)

with tab3:
    # 재유입 로직: 180일 기준
    df_history = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_history['구매간격'] = (df_history['매출일자'] - df_history.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    winback_data_all = df_history[(df_history['사업자번호'].isin(df_final['사업자번호'])) & (df_history['구매간격'] >= 180)].copy()
    winback_ids = winback_data_all['사업자번호'].unique()

    if len(winback_ids) > 0:
        sales_curr_wb = df_final[df_final['사업자번호'].isin(winback_ids)].groupby('사업자번호')['매출액'].sum()
        sales_hist_wb = df_raw[df_raw['사업자번호'].isin(winback_ids)].groupby('사업자번호')['매출액'].max()
        df_wb_sum = pd.DataFrame({'올해매출': sales_curr_wb, '과거최고': sales_hist_wb})
        df_wb_sum['회복률'] = (df_wb_sum['올해매출'] / df_wb_sum['과거최고'] * 100).fillna(0)
        avg_recovery = df_wb_sum['회복률'].mean()
        full_recovery_cnt = len(df_wb_sum[df_wb_sum['회복률'] >= 80])
        
        # 복귀 트리거 제품 찾기
        trigger_prod = winback_data_all.groupby('제품명').size().idxmax()

        st.markdown(f"""
        **[📊 데이터 요약]**
        * 총 재유입 거래처: **{len(winback_ids)} 처** (6개월 공백 후 복귀 기준)
        * 재유입 발생 총 매출: **{df_wb_sum['올해매출'].sum():,.0f} 백만원**
        * 평균 회복률: **{avg_recovery:.1f}%** (과거 최고 매출 대비 현재 매출 비율)
        * 그룹별 분포: **완전 회복({full_recovery_cnt}처), 회복 중({len(df_wb_sum[(df_wb_sum['회복률']<80)&(df_wb_sum['회복률']>=20)])}처), 재진입 초기({len(df_wb_sum[df_wb_sum['회복률']<20])}처)**
        """)
        
        st.info(f"올해 재유입된 거래처의 평균 회복률은 **{avg_recovery:.1f}%**입니다. 과거 전성기 대비 80% 이상 실적을 회복한 '완전 회복' 그룹이 **{full_recovery_cnt}처**로 파악됩니다. 특히 이탈 고객들이 복귀 시 가장 먼저 찾는 제품은 **{trigger_prod}**입니다.")

    render_winback_quality(df_final, df_raw, sel_years[0])
    st.markdown("---")
    st.markdown("### 🔄 재유입 기여 비중 및 이탈 전 구매 품목")
    df_f = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_f['이전_제품'] = df_f.groupby('사업자번호')['제품명'].shift(1)
    df_f['구매간격'] = (df_f['매출일자'] - df_f.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    res = df_final.merge(df_f[['사업자번호', '매출일자', '이전_제품', '구매간격']], on=['사업자번호', '매출일자'], how='left')
    res = res[res['구매간격'] >= 180]
    
    if not res.empty:
        col_pie, col_table = st.columns([1, 1])
        with col_pie: st.plotly_chart(px.pie(res, values='매출액', names='제품명', title="재유입 매출 기여 비중"), use_container_width=True)
        with col_table:
            res_sum = res.groupby('제품명').agg({'사업자번호': 'nunique', '매출액': 'sum'}).reset_index().sort_values('사업자번호', ascending=False)
            st.markdown('<p class="guide-text">💡 제품명을 클릭하면 하단에 과거 패턴이 표시됩니다.</p>', unsafe_allow_html=True)
            ev_res = st.dataframe(res_sum.rename(columns={'사업자번호':'재유입처수', '매출액':'매출액(백만원)'}).style.format({'매출액(백만원)': '{:,.1f}'}), use_container_width=True, on_select="rerun", selection_mode="single-row")
        if len(ev_res.selection.rows) > 0:
            s_p = res_sum.iloc[ev_res.selection.rows[0]]['제품명']
            st.plotly_chart(px.bar(res[res['제품명'] == s_p].groupby('이전_제품').size().reset_index(name='count').sort_values('count', ascending=False).head(10), x='count', y='이전_제품', orientation='h', title=f"[{s_p}] 복귀 고객의 과거 주력 제품"), use_container_width=True)

with tab4:
    reg_stats = df_final.groupby('지역').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    top_cnt_reg = reg_stats.loc[reg_stats['Count'].idxmax(), '지역']
    top_sales_reg = reg_stats.loc[reg_stats['Sales'].idxmax(), '지역']
    
    st.markdown(f"""
    **[📊 데이터 요약]**
    * 최다 거래 지역: **{top_cnt_reg}** ({reg_stats['Count'].max()} 처)
    * 최고 매출 지역: **{top_sales_reg}** ({reg_stats['Sales'].max():,.0f} 백만원)
    """)
    
    # 지역 리스크 및 효율 인사이트
    risk_list = []
    for r in df_final['지역'].unique():
        r_df = df_final[df_final['지역'] == r]
        risk_list.append({'지역': r, '의존도': (r_df.groupby('거래처명')['매출액'].sum().max() / r_df['매출액'].sum() * 100)})
    df_risk = pd.DataFrame(risk_list).sort_values('의존도', ascending=False)
    high_risk_reg = df_risk.iloc[0]['지역']
    high_risk_val = df_risk.iloc[0]['의존도']
    
    st.warning(f"**{high_risk_reg}** 지역은 상위 1개 병원의 매출 비중이 **{high_risk_val:.1f}%**에 달해 거점 의존도가 매우 높습니다. 또한 **{top_cnt_reg}** 지역은 거래처 수는 많으나 매출 효율 개선이 필요합니다.")

    render_regional_deep_dive(df_final)
    st.markdown("---")
    st.markdown("### 🗺️ 지역별 상세 실적 리스트")
    reg_s = df_final.groupby('지역').agg({'매출액': 'sum', '사업자번호': 'nunique'}).reset_index().sort_values('매출액', ascending=False)
    c_r1, c_r2 = st.columns([1, 1.5])
    with c_r1:
        st.markdown('<p class="guide-text">💡 지역 선택 시 우측 비중과 하단 데이터가 연동됩니다.</p>', unsafe_allow_html=True)
        st.dataframe(reg_s.rename(columns={'사업자번호':'구매처수'}).style.format({'매출액': '{:,.0f} 백만원'}), use_container_width=True)
        sel_reg = st.selectbox("🔎 분석할 지역 선택", reg_s['지역'].unique(), key="p4_reg_orig")
    with c_r2:
        if sel_reg: st.plotly_chart(px.pie(df_final[df_final['지역'] == sel_reg], values='매출액', names='제품명', hole=0.3, title=f"[{sel_reg}] 제품 비중"), use_container_width=True)
    if sel_reg:
        r_agg = df_final[df_final['지역'] == sel_reg].groupby(['거래처명', '제품명']).agg({'매출액': 'sum', '수량': 'sum'}).reset_index().sort_values('매출액', ascending=False).head(50)
        st.dataframe(r_agg.style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True)

with tab5:
    p_main = df_final.groupby('제품명').agg({'수량': 'sum', '매출액': 'sum', '사업자번호': 'nunique'}).reset_index().sort_values('매출액', ascending=False)
    top_prod = p_main.iloc[0]['제품명']
    cat_main = df_final.groupby('제품군')['매출액'].sum().reset_index()
    top_cat = cat_main.loc[cat_main['매출액'].idxmax(), '제품군']
    cat_ratio = (cat_main['매출액'].max() / cat_main['매출액'].sum() * 100)
    penetration = (p_main['사업자번호'].sum() / df_final['사업자번호'].nunique() * 100)

    st.markdown(f"""
    **[📊 데이터 요약]**
    * 최다 판매 제품: **{top_prod}** ({p_main.iloc[0]['수량']:,}개 / {p_main.iloc[0]['매출액']:,.0f} 백만원)
    * 최대 매출 제품군: **{top_cat}** (전체 매출의 **{cat_ratio:.1f}%**)
    * 시장 침투율: 전체 거래처 중 **{penetration:.1f}%**가 최소 1개 이상의 제품을 구매 중
    """)
    
    st.info(f"현재 **{top_prod}**이 매출 성장을 견인하고 있으며, **{top_cat}** 제품군이 전체 실적의 핵심 축을 담당하고 있습니다. 제품별 성장 잠재력을 BCG 매트릭스에서 확인하세요.")

    render_product_strategy(df_final)
    st.markdown("---")
    st.markdown("### 📦 제품별 판매 현황")
    st.markdown('<p class="guide-text">💡 체크박스 클릭 시 하단에 상세 병원 리스트가 표시됩니다.</p>', unsafe_allow_html=True)
    ev_p = st.dataframe(p_main.rename(columns={'사업자번호':'구매처수'}).style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True, on_select="rerun", selection_mode="single-row", height=300)
    if len(ev_p.selection.rows) > 0:
        sel_p_name = p_main.iloc[ev_p.selection.rows[0]]['제품명']
        p_detail = df_final[df_final['제품명'] == sel_p_name].groupby('거래처명').agg({'매출액': 'sum'}).reset_index().sort_values('매출액', ascending=False)
        st.dataframe(p_detail.style.format({'매출액': '{:,.1f} 백만원'}), use_container_width=True)
