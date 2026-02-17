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
        df = pd.read_excel(io.BytesIO(response.content), engine='openpyxl')
    except Exception as e:
        st.error(f"❌ 로드 오류: {e}"); return pd.DataFrame()

    df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
    col_map = {'매출일자':['매출일자','날짜','Date'],'제품명':['제품명변환','제품명'],'합계금액':['합계금액','매출액'],'수량':['수량','Qty'],'사업자번호':['사업자번호','BizNo'],'거래처명':['거래처명','병원명'],'진료과':['진료과'],'제품군':['제품군'],'지역':['지역']}
    for std, candidates in col_map.items():
        if std not in df.columns:
            for cand in candidates:
                if cand in df.columns: df.rename(columns={cand: std}, inplace=True); break
    try:
        df['매출일자'] = pd.to_datetime(df['매출일자'], errors='coerce')
        df = df.dropna(subset=['매출일자'])
        df['년'] = df['매출일자'].dt.year.astype(np.int16)
        df['분기'] = df['매출일자'].dt.quarter.astype(np.int8)
        df['월'] = df['매출일자'].dt.month.astype(np.int8)
        df['년월'] = df['매출일자'].dt.strftime('%Y-%m')
        df['매출액'] = (pd.to_numeric(df.get('합계금액', 0), errors='coerce').fillna(0) / 1000000).astype(np.float32)
        df['수량'] = pd.to_numeric(df.get('수량', 0), errors='coerce').fillna(0).astype(np.int32)
        def classify_channel(group): return 'online' if group in ['B2B', 'SAP', '의사회원'] else 'offline'
        if '거래처그룹' in df.columns: df['판매채널'] = df['거래처그룹'].apply(classify_channel)
        for col in ['거래처명', '제품명', '제품군', '진료과', '지역']:
            if col in df.columns: df[col] = df[col].astype(str).replace('nan', '미분류')
        if '제품명' in df.columns: df['제품명'] = df['제품명'].str.replace(r'\(.*?\)', '', regex=True).str.strip()
    except: return pd.DataFrame()
    return df

@st.cache_data
def classify_customers(df, target_year):
    cols_to_agg = {'거래처명': 'last', '매출일자': 'max'}
    if '진료과' in df.columns: cols_to_agg['진료과'] = 'last'
    if '지역' in df.columns: cols_to_agg['지역'] = 'last'
    cust_year = df.groupby(['사업자번호', '년']).size().unstack(fill_value=0)
    base_info = df.sort_values('매출일자').groupby('사업자번호').agg(cols_to_agg).rename(columns={'매출일자': '최근구매일'})
    sales_ty = df[df['년'] == target_year].groupby('사업자번호')['매출액'].sum()
    base_info['해당년도_매출'] = base_info.index.map(sales_ty).fillna(0)
    classification = {}
    for biz_no in base_info.index:
        has_ty = (target_year in cust_year.columns) and (cust_year.loc[biz_no, target_year] > 0)
        has_t1 = (target_year - 1 in cust_year.columns) and (cust_year.loc[biz_no, target_year - 1] > 0)
        has_h = cust_year.loc[biz_no, [y for y in cust_year.columns if y < target_year - 1]].sum() > 0 if len(cust_year.columns)>1 else False
        if has_ty: status = "✅ 기존 (유지)" if has_t1 else ("🔄 재유입 (복귀)" if has_h else "🆕 신규 (New)")
        else: status = "📉 1년 이탈" if has_t1 else "💤 장기 이탈"
        classification[biz_no] = status
    base_info['상태'] = base_info.index.map(classification)
    return base_info

# --------------------------------------------------------------------------------
# 3. 데이터 로드 및 필터링
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
df_raw = load_data_from_drive(DRIVE_FILE_ID)
if df_raw.empty: st.stop()

sel_years = get_p('y', [df_raw['년'].max()])
sel_channels = get_p('c', sorted(df_raw['판매채널'].unique()))
sel_quarters = get_p('q', sorted(df_raw['분기'].unique()))
sel_months = get_p('m', sorted(df_raw['월'].unique()))
sel_cats = get_p('cat', sorted(df_raw['제품군'].unique()), df_raw, '제품군')
sel_products = get_p('prod', sorted(df_raw['제품명'].unique()), df_raw, '제품명')

if is_edit_mode:
    with st.sidebar:
        st.header("⚙️ 필터 설정")
        sel_channels = st.multiselect("판매채널", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        sel_quarters = st.multiselect("분기", sorted(df_raw['분기'].unique()), default=sel_quarters)
        sel_months = st.multiselect("월", sorted(df_raw['월'].unique()), default=sel_months)
        sel_cats = st.multiselect("제품군", sorted(df_raw['제품군'].unique()), default=sel_cats)
        sel_products = st.multiselect("제품명", sorted(df_raw['제품명'].unique()), default=sel_products)
        if st.button("🔗 축약 공유 링크 생성"):
            base_url = "https://skbs-sales-2026-cbktkdtxsyrfzfrihefs2h.streamlit.app/"
            cat_p = "all" if len(sel_cats) == len(df_raw['제품군'].unique()) else "&cat=".join([urllib.parse.quote(x) for x in sel_cats])
            prod_p = "all" if len(sel_products) == len(df_raw['제품명'].unique()) else "&prod=".join([urllib.parse.quote(x) for x in sel_products])
            p_string = f"?y={'&y='.join(map(str, sel_years))}&c={'&c='.join(sel_channels)}&q={'&q='.join(map(str, sel_quarters))}&m={'&m='.join(map(str, sel_months))}&cat={cat_p}&prod={prod_p}"
            st.code(base_url + p_string)

df_final = df_raw[(df_raw['년'].isin(sel_years)) & (df_raw['판매채널'].isin(sel_channels)) & (df_raw['분기'].isin(sel_quarters)) & (df_raw['월'].isin(sel_months)) & (df_raw['제품군'].isin(sel_cats)) & (df_raw['제품명'].isin(sel_products))]

# --------------------------------------------------------------------------------
# 4. 분석 함수 정의
# --------------------------------------------------------------------------------
def render_smart_overview(df_curr, df_raw):
    if df_curr.empty: return
    cur_y = int(df_curr['년'].max()); last_y = cur_y - 1
    df_prev = df_raw[(df_raw['년'] == last_y) & (df_raw['월'].isin(df_curr['월'].unique()))]
    sales_curr, sales_prev = df_curr['매출액'].sum(), df_prev['매출액'].sum()
    sales_pct = ((sales_curr - sales_prev) / (sales_prev if sales_prev > 0 else 1) * 100)
    cust_curr, cust_prev = set(df_curr['사업자번호']), set(df_prev['사업자번호'])
    new_c, lost_c = len(cust_curr - cust_prev), len(cust_prev - cust_curr)
    st.markdown(f"### 🚀 {cur_y}년 Summary")
    st.info("💡 분석 지표 기준: 신규(전년무→올해유), 이탈(전년유→올해무), 유지율")
    c1, c2, c3 = st.columns(3)
    c1.metric("💰 총 매출 실적", f"{sales_curr:,.0f}M", f"{sales_pct:+.1f}%")
    c2.metric("🏥 총 거래 병원", f"{len(cust_curr):,} 처"); st.markdown(f"- ✨신규: +{new_c} / 💔이탈: -{lost_c}")
    c3.metric("🏆 Best Product", df_curr.groupby('제품명')['매출액'].sum().idxmax())

def render_winback_quality(df_v, df_r, current_year):
    st.markdown(f"### ♻️ {current_year}년 재유입 현황 분석")
    df_h = df_r.sort_values(['사업자번호', '매출일자']).copy()
    df_h['구매간격'] = (df_h['매출일자'] - df_h.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    wb_v = df_h[(df_h['사업자번호'].isin(df_v['사업자번호'])) & (df_h['구매간격'] >= 180)].copy()
    if wb_v.empty: st.info("재유입 데이터 없음"); return
    wb_ids = wb_v['사업자번호'].unique()
    s_c = df_v[df_v['사업자번호'].isin(wb_ids)].groupby('사업자번호')['매출액'].sum()
    s_m = df_r[df_r['사업자번호'].isin(wb_ids)].groupby('사업자번호')['매출액'].max()
    df_wb_res = pd.DataFrame({'올해': s_c, '최고': s_m}).fillna(0)
    df_wb_res['회복률'] = (df_wb_res['올해'] / df_wb_res['최고'] * 100).replace([np.inf, -np.inf], 0)
    df_wb_res['상태'] = df_wb_res['회복률'].apply(lambda x: "완전 회복" if x>=80 else ("회복 중" if x>=20 else "재진입 초기"))
    df_wb_res['거래처명'] = df_v[df_v['사업자번호'].isin(wb_ids)].groupby('사업자번호')['거래처명'].last()
    df_wb_res['bubble'] = df_wb_res['올해'].apply(lambda x: max(x, 0.1))
    c1, c2 = st.columns([1, 1])
    with c1: st.plotly_chart(px.scatter(df_wb_res, x='최고', y='올해', color='상태', hover_name='거래처명', size='bubble', color_discrete_map={"완전 회복":"green","회복 중":"orange","재진입 초기":"red"}), use_container_width=True)
    with c2: event_wb = st.dataframe(df_wb_res[['상태', '거래처명', '올해', '회복률']], use_container_width=True, on_select="rerun", selection_mode="single-row")
    if len(event_wb.selection.rows) > 0:
        sel_biz = df_wb_res.index[event_wb.selection.rows[0]]
        st.dataframe(df_h[df_h['사업자번호']==sel_biz].sort_values('매출일자', ascending=False), use_container_width=True)

def render_regional_deep_dive(df):
    if df.empty: return
    reg_stats = df.groupby('지역').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    reg_stats['Per'] = reg_stats['Sales'] / reg_stats['Count']
    st.markdown("### 🗺️ 지역별 심층 분석")
    st.plotly_chart(px.scatter(reg_stats, x='Count', y='Per', size='Sales', color='지역', text='지역', labels={'Count': '거래처 수', 'Per': '평균 객단가'}), use_container_width=True)

def render_product_strategy(df):
    if df.empty: return
    st.markdown("### 💊 제품별 전략 분석 (BCG & Seasonality)")
    p_stats = df.groupby('제품명').agg(Sales=('매출액', 'sum'), Count=('사업자번호', 'nunique')).reset_index()
    c1, c2 = st.columns(2)
    with c1: st.plotly_chart(px.scatter(p_stats, x='Count', y='Sales', size='Sales', color='제품명', text='제품명', title="BCG 매트릭스"), use_container_width=True)
    with c2:
        pivot = df.pivot_table(index='제품명', columns='월', values='매출액', aggfunc='sum', fill_value=0)
        st.plotly_chart(px.imshow(pivot.div(pivot.max(axis=1), axis=0), color_continuous_scale="Reds", title="제품별 판매 시즌 집중도"), use_container_width=True)
    with st.expander("🧩 함께 팔기(Cross-selling) 기회 분석기", expanded=True):
        all_p = sorted(df['제품명'].unique())
        col1, col2 = st.columns(2)
        b_p = col1.selectbox("Anchor 제품 (이미 쓰는 것)", all_p, index=0)
        t_p = col2.selectbox("Target 제품 (팔고 싶은 것)", all_p, index=min(1, len(all_p)-1))
        if b_p != t_p:
            targets = list(set(df[df['제품명']==b_p]['거래처명'].unique()) - set(df[df['제품명']==t_p]['거래처명'].unique()))
            st.metric("🎯 추가 영업 기회", f"{len(targets)} 처")
            if targets:
                t_info = df[(df['거래처명'].isin(targets)) & (df['제품명']==b_p)].groupby(['거래처명','지역'])['매출액'].sum().reset_index().sort_values('매출액', ascending=False)
                st.dataframe(t_info.head(50), use_container_width=True)

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 1. Overview", "🏆 2. 매출 상위 & 이탈 관리", "🔄 3. 재유입 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석"])

with tab1: render_smart_overview(df_final, df_raw)

with tab2:
    if not df_final.empty:
        total_s = df_final['매출액'].sum()
        top100 = df_final.groupby(['사업자번호', '거래처명', '진료과']).agg({'매출액': 'sum'}).sort_values('매출액', ascending=False).head(100).reset_index()
        cls_d = classify_customers(df_raw, sel_years[0]); st_c = cls_d['상태'].value_counts()
        last_p = df_raw.groupby('사업자번호')['매출일자'].max(); top100['최근구매일'] = top100['사업자번호'].map(last_p)
        cur_d = df_raw['매출일자'].max(); risk_vips = top100[(cur_d - top100['최근구매일']).dt.days >= 90]
        risk_cnt = len(risk_vips)
        top_v = top100.iloc[0]

        st.subheader("📊 데이터 요약")
        st.write(f"• 상위 100대 매출 합계: **{top100['매출액'].sum()/100:,.1f}억** (비중 {(top100['매출액'].sum()/total_s*100):.1f}%)")
        st.write(f"• 거래처 상태 분포: **기존({st_c.get('✅ 기존 (유지)',0)}처), 신규({st_c.get('🆕 신규 (New)',0)}처), 재유입({st_c.get('🔄 재유입 (복귀)',0)}처), 이탈({st_c.get('📉 1년 이탈',0)}처)**")
        st.write(f"• 평균 객단가: 처당 약 **{(total_s/df_final['사업자번호'].nunique()*100):,.0f}만원**")
        st.write(f"• 최고 매출 거래처: **{top_v['거래처명']}** ({top_v['매출액']:,.1f} 백만원)")
        
        st.subheader("💡 스마트 인사이트")
        st.write(f"• **VIP 이탈 위험 알림:** 상위 100대 중 **{risk_cnt}처({risk_cnt}%)**가 90일 이상 공백 상태입니다. 특히 **{top_v['거래처명']}**의 관리가 시급합니다.")
        st.write(f"• **신규 vs 이탈 밸런스:** 금기 신규 유입({st_c.get('🆕 신규 (New)',0)}처) 대비 이탈({st_c.get('📉 1년 이탈',0)}처) 관리 전략이 우선시됩니다.")
        st.write(f"• **진료과 집중도 리스크:** 매출의 {(df_final.groupby('진료과')['매출액'].sum().max()/total_s*100):.1f}%가 **{df_final.groupby('진료과')['매출액'].sum().idxmax()}**에 편중되어 있습니다.")
        
    st.markdown('<p class="guide-text">💡 아래 표에서 행을 클릭하면 하단에 상세 실적이 표시됩니다.</p>', unsafe_allow_html=True)
    top100['상태'] = (cur_d - top100['최근구매일']).dt.days.apply(lambda x: '🚨 이탈위험' if x >= 90 else '✅ 정상')
    top100['최근구매일_str'] = top100['최근구매일'].dt.strftime('%Y-%m-%d')
    event_v = st.dataframe(top100[['상태', '거래처명', '진료과', '매출액', '최근구매일_str']], use_container_width=True, on_select="rerun", selection_mode="single-row")
    if len(event_v.selection.rows) > 0:
        sel_biz = top100.iloc[event_v.selection.rows[0]]['사업자번호']
        st.dataframe(df_raw[df_raw['사업자번호'] == sel_biz].groupby('제품명').agg({'매출액':'sum'}).sort_values('매출액', ascending=False), use_container_width=True)

with tab3:
    df_h_sum = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_h_sum['구매간격'] = (df_h_sum['매출일자'] - df_h_sum.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    wb_v_sum = df_h_sum[(df_h_sum['사업자번호'].isin(df_final['사업자번호'])) & (df_h_sum['구매간격'] >= 180)].copy()
    if not wb_v_sum.empty:
        wb_ids = wb_v_sum['사업자번호'].unique()
        df_wb_f = pd.DataFrame({'올해': df_final[df_final['사업자번호'].isin(wb_ids)].groupby('사업자번호')['매출액'].sum(), '최고': df_raw[df_raw['사업자번호'].isin(wb_ids)].groupby('사업자번호')['매출액'].max()}).fillna(0)
        df_wb_f['회복률'] = (df_wb_f['올해'] / df_wb_f['최고'] * 100).replace([np.inf, -np.inf], 0)
        full_rec = len(df_wb_f[df_wb_f['회복률']>=80]); mid_rec = len(df_wb_f[(df_wb_f['회복률']<80)&(df_wb_f['회복률']>=20)]); low_rec = len(df_wb_f[df_wb_f['회복률']<20])
        trig_p = wb_v_sum.groupby('제품명').size().idxmax()
        
        st.subheader("📊 데이터 요약")
        st.write(f"• 총 재유입 거래처: **{len(wb_ids)} 처** (6개월 공백 기준)")
        st.write(f"• 재유입 발생 총 매출: **{df_wb_f['올해'].sum():,.0f} 백만원**")
        st.write(f"• 평균 회복률: **{df_wb_f['회복률'].mean():.1f}%** / 그룹분포: 완전({full_rec}), 회복중({mid_rec}), 초기({low_rec})")
        st.subheader("💡 스마트 인사이트")
        st.write(f"• **회복 퀄리티 요약:** 올해 재유입 고객의 평균 회복률은 **{df_wb_f['회복률'].mean():.1f}%**입니다. '완전 회복' 그룹이 **{full_rec}처**로 충성도가 정상화되고 있습니다.")
        st.write(f"• **복귀 트리거 제품:** 이탈 고객들이 복귀 시 가장 먼저 찾는 제품은 **{trig_p}**입니다. SMS 마케팅 시 이 제품을 제안하세요.")
        st.write(f"• **재유입 매출 기여도:** 전체 매출 중 재유입 거래처 비중은 **{(df_wb_f['올해'].sum()/df_final['매출액'].sum()*100):.1f}%**입니다.")
    render_winback_quality(df_final, df_raw, sel_years[0])

with tab4:
    reg_v = df_final.groupby('지역').agg(Sales=('매출액','sum'), Count=('사업자번호','nunique')).reset_index().sort_values('Sales', ascending=False)
    y_v = int(df_final['년'].max()); m_v = df_final['월'].unique()
    df_ly_r = df_raw[(df_raw['년'] == y_v-1) & (df_raw['월'].isin(m_v))].groupby('지역').agg(LY_S=('매출액','sum'), LY_C=('사업자번호','nunique')).reset_index()
    reg_sum = reg_v.merge(df_ly_r, on='지역', how='left').fillna(0)
    inc_r = reg_sum.loc[(reg_sum['Count']-reg_sum['LY_C']).idxmax()]; dec_r = reg_sum.loc[(reg_sum['Count']-reg_sum['LY_C']).idxmin()]

    st.subheader("📊 데이터 요약")
    if not reg_v.empty:
        st.write(f"• 최다 거래 지역: **{reg_v.sort_values('Count', ascending=False).iloc[0]['지역']}** / 최고 매출 지역: **{reg_v.iloc[0]['지역']}**")
        st.write(f"• 거래처 변동: 전년비 **{inc_r['지역']} {int(inc_r['Count']-inc_r['LY_C'])}처 증가** / **{dec_r['지역']} {abs(int(dec_r['Count']-dec_r['LY_C']))}처 감소**")
        st.subheader("💡 스마트 인사이트")
        st.write(f"• **커버리지 변동:** **{inc_r['지역']}**은 전년비 거래처가 **{int(inc_r['Count']-inc_r['LY_C'])}처** 증가하며 확장을 보이고 있습니다.")
        st.write(f"• **지역별 거점 의존도:** **{reg_v.iloc[0]['지역']}** 지역의 상위 거점 병원 매출 비중 관리가 필요합니다.")
    render_regional_deep_dive(df_final)
    st.markdown("### 🗺️ 지역별 상세 리스트"); sel_r = st.selectbox("🔎 지역 선택", reg_v['지역'].unique(), key="p4_sel")
    col_r1, col_r2 = st.columns([1, 1.5])
    with col_r1: st.dataframe(reg_v.rename(columns={'Count':'구매처수'}), use_container_width=True)
    with col_r2: st.plotly_chart(px.pie(df_final[df_final['지역']==sel_r], values='매출액', names='제품명', hole=0.3, title="지역 제품 비중"), use_container_width=True)
    st.dataframe(df_final[df_final['지역']==sel_r].groupby(['거래처명','제품명']).agg({'매출액':'sum','수량':'sum'}).sort_values('매출액', ascending=False).head(50), use_container_width=True)

with tab5:
    p_v = df_final.groupby('제품명').agg(Sales=('매출액','sum'), Qty=('수량', 'sum'), Count=('사업자번호','nunique')).reset_index().sort_values('Sales', ascending=False)
    cat_v = df_final.groupby('제품군')['매출액'].sum().reset_index().sort_values('매출액', ascending=False)
    # 성장률 연산
    ly_p = df_raw[(df_raw['년']==int(df_final['년'].max())-1)&(df_raw['월'].isin(df_final['월'].unique()))].groupby('제품명')['매출액'].sum()
    p_v['Growth'] = (p_v['Sales'] - p_v['제품명'].map(ly_p).fillna(0)) / p_v['제품명'].map(ly_p).replace(0,1) * 100
    top_g = p_v.sort_values('Growth', ascending=False).iloc[0]
    
    st.subheader("📊 데이터 요약")
    if not p_v.empty:
        st.write(f"• 최다 판매 제품: **{p_v.iloc[0]['제품명']}** ({int(p_v.iloc[0]['Qty']):,}개 / {p_v.iloc[0]['Sales']:,.0f} 백만원)")
        st.write(f"• 성장률 1위 제품: **{top_g['제품명']}** (전기 대비 **{top_g['Growth']:+.1f}%**)")
        st.write(f"• 최대 매출 제품군: **{cat_v.iloc[0]['제품군']}** (비중 **{(cat_v.iloc[0]['매출액']/df_final['매출액'].sum()*100):.1f}%**)")
        st.write(f"• 시장 침투율: 전체 거래처 중 **{(len(df_final['사업자번호'].unique())/len(df_raw['사업자번호'].unique())*100):.1f}%**가 구매 중")
        st.subheader("💡 스마트 인사이트")
        st.write(f"• **제품별 요약:** **{p_v.iloc[0]['제품명']}**이 성장을 견인 중이며, **{p_v.iloc[-1]['제품명']}**은 성숙기 전략이 필요합니다.")
        if len(p_v) >= 2:
            acc_A = set(df_final[df_final['제품명']==p_v.iloc[0]['제품명']]['사업자번호'].unique())
            acc_B = set(df_final[df_final['제품명']==p_v.iloc[1]['제품명']]['사업자번호'].unique())
            st.write(f"• **교차 판매 기회:** **{p_v.iloc[0]['제품명']}** 사용처 중 **{p_v.iloc[1]['제품명']}** 미구매 타겟 **{len(acc_A - acc_B)}처**를 확보했습니다.")

    st.markdown("### 📦 제품별 판매 현황")
    g1, g2 = st.columns(2)
    with g1: st.plotly_chart(px.bar(p_v.head(10), x='Sales', y='제품명', orientation='h', title="제품 매출 Top 10"), use_container_width=True)
    with g2: st.plotly_chart(px.pie(cat_v, values='매출액', names='제품군', hole=0.3, title="제품군 매출 비중"), use_container_width=True)
    st.markdown('<p class="guide-text">💡 아래 표에서 행을 클릭하면 상세 병원 리스트가 표시됩니다.</p>', unsafe_allow_html=True)
    ev_p = st.dataframe(p_v.rename(columns={'Count':'구매처수'}), use_container_width=True, on_select="rerun", selection_mode="single-row", height=300)
    if len(ev_p.selection.rows) > 0:
        sel_p = p_v.iloc[ev_p.selection.rows[0]]['제품명']
        st.dataframe(df_final[df_final['제품명']==sel_p].groupby('거래처명').agg({'매출액':'sum'}).sort_values('매출액', ascending=False), use_container_width=True)
    st.markdown("---"); render_product_strategy(df_final)
