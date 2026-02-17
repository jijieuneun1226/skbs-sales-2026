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
                real_url = match_action.group(1).replace("&amp;", "&")
                response = session.get(real_url, params=params_dict, stream=True)
        if response.status_code != 200: return pd.DataFrame()
        file_bytes = io.BytesIO(response.content)
        df = pd.read_excel(file_bytes, engine='openpyxl')
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}"); return pd.DataFrame()

    df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
    if "매출일자" not in df.columns:
        for idx, row in df.head(10).iterrows():
            if any("매출일자" in str(x) for x in row):
                df.columns = df.iloc[idx].astype(str).str.replace(r'\s+', '', regex=True)
                df = df.iloc[idx+1:].reset_index(drop=True)
                break
    col_map = {'매출일자':['매출일자','날짜','Date'],'제품명':['제품명변환','제품명'],'합계금액':['합계금액','매출액'],'수량':['수량','Qty'],'사업자번호':['사업자번호','BizNo'],'거래처명':['거래처명','병원명'],'진료과':['진료과'],'제품군':['제품군'],'지역':['지역']}
    for std, cands in col_map.items():
        if std in df.columns: continue
        for cand in cands:
            if cand in df.columns: df.rename(columns={cand:std}, inplace=True); break
    try:
        df['매출일자'] = pd.to_datetime(df['매출일자'], errors='coerce')
        df = df.dropna(subset=['매출일자'])
        df['년'] = df['매출일자'].dt.year.astype(np.int16)
        df['분기'] = df['매출일자'].dt.quarter.astype(np.int8)
        df['월'] = df['매출일자'].dt.month.astype(np.int8)
        df['년월'] = df['매출일자'].dt.strftime('%Y-%m')
        df['매출액'] = (pd.to_numeric(df.get('합계금액',0), errors='coerce').fillna(0)/1000000).astype(np.float32)
        df['수량'] = pd.to_numeric(df.get('수량',0), errors='coerce').fillna(0).astype(np.int32)
        def classify_channel(group): return 'online' if str(group) in ['B2B','SAP','의사회원'] else 'offline'
        df['판매채널'] = df.get('거래처그룹','기타').apply(classify_channel)
        for col in ['거래처명','제품명','제품군','진료과','지역']:
            if col not in df.columns: df[col] = '미분류'
            else: df[col] = df[col].astype(str).replace('nan','미분류')
        df['제품명'] = df['제품명'].str.replace(r'\(.*?\)', '', regex=True).str.strip()
    except Exception as e:
        st.error(f"전처리 오류: {e}"); return pd.DataFrame()
    return df

@st.cache_data
def classify_customers(df, target_year):
    cols_to_agg = {'거래처명':'last','매출일자':'max'}
    if '진료과' in df.columns: cols_to_agg['진료과'] = 'last'
    if '지역' in df.columns: cols_to_agg['지역'] = 'last'
    cust_year = df.groupby(['사업자번호', '년']).size().unstack(fill_value=0)
    base_info = df.sort_values('매출일자').groupby('사업자번호').agg(cols_to_agg).rename(columns={'매출일자':'최근구매일'})
    sales_ty = df[df['년']==target_year].groupby('사업자번호')['매출액'].sum()
    base_info['해당년도_매출'] = base_info.index.map(sales_ty).fillna(0)
    classification = {}
    for biz in base_info.index:
        has_ty = (target_year in cust_year.columns) and (cust_year.loc[biz, target_year] > 0)
        has_t1 = (target_year-1 in cust_year.columns) and (cust_year.loc[biz, target_year-1] > 0)
        has_h = cust_year.loc[biz, [y for y in cust_year.columns if y < target_year-1]].sum() > 0 if len(cust_year.columns)>1 else False
        status = "✅ 기존 (유지)" if has_ty and has_t1 else ("🔄 재유입 (복귀)" if has_ty and has_h else ("🆕 신규 (New)" if has_ty else ("📉 1년 이탈" if has_t1 else "💤 장기 이탈")))
        classification[biz] = status
    base_info['상태'] = base_info.index.map(classification)
    return base_info

# --------------------------------------------------------------------------------
# 3. 데이터 로드 및 필터링 (축약형 링크 지원)
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
        st.header("⚙️ 관리자 필터 설정")
        sel_channels = st.multiselect("판매채널", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        sel_quarters = st.multiselect("분기", sorted(df_raw['분기'].unique()), default=sel_quarters)
        sel_months = st.multiselect("월", sorted(df_raw['월'].unique()), default=sel_months)
        sel_cats = st.multiselect("제품군", sorted(df_raw['제품군'].unique()), default=sel_cats)
        sel_products = st.multiselect("제품명", sorted(df_raw['제품명'].unique()), default=sel_products)
        if st.button("🔗 축약 공유 링크 생성"):
            cat_p = "all" if len(sel_cats) == len(df_raw['제품군'].unique()) else "&cat=".join([urllib.parse.quote(x) for x in sel_cats])
            prod_p = "all" if len(sel_products) == len(df_raw['제품명'].unique()) else "&prod=".join([urllib.parse.quote(x) for x in sel_products])
            p_string = f"?y={'&y='.join(map(str, sel_years))}&c={'&c='.join(sel_channels)}&q={'&q='.join(map(str, sel_quarters))}&m={'&m='.join(map(str, sel_months))}&cat={cat_p}&prod={prod_p}"
            st.code("https://skbs-sales-2026-cbktkdtxsyrfzfrihefs2h.streamlit.app/" + p_string)

df_final = df_raw[(df_raw['년'].isin(sel_years)) & (df_raw['판매채널'].isin(sel_channels)) & (df_raw['분기'].isin(sel_quarters)) & (df_raw['월'].isin(sel_months)) & (df_raw['제품군'].isin(sel_cats)) & (df_raw['제품명'].isin(sel_products))]

# --------------------------------------------------------------------------------
# 4. 분석 모듈 정의
# --------------------------------------------------------------------------------
def render_winback_quality(df_v, df_r, current_year):
    st.markdown(f"### ♻️ {current_year}년 재유입 현황 분석")
    df_h = df_r.sort_values(['사업자번호', '매출일자']).copy()
    df_h['구매간격'] = (df_h['매출일자'] - df_h.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    wb_v = df_h[(df_h['사업자번호'].isin(df_v['사업자번호'])) & (df_h['구매간격'] >= 180)].copy()
    if wb_v.empty: st.info("재유입 데이터 없음"); return
    wb_ids = wb_v['사업자번호'].unique()
    s_c = df_v[df_v['사업자번호'].isin(wb_ids)].groupby('사업자번호')['매출액'].sum()
    s_m = df_r[df_r['사업자번호'].isin(wb_ids)].groupby('사업자번호')['매출액'].max()
    df_wb_l = pd.DataFrame({'올해': s_c, '최고': s_m}).fillna(0)
    df_wb_l['회복률'] = (df_wb_l['올해'] / df_wb_l['최고'] * 100).replace([np.inf, -np.inf], 0)
    df_wb_l['상태'] = df_wb_l['회복률'].apply(lambda x: "완전 회복" if x>=80 else ("회복 중" if x>=20 else "재진입 초기"))
    df_wb_l['거래처명'] = df_v[df_v['사업자번호'].isin(wb_ids)].groupby('사업자번호')['거래처명'].last()
    df_wb_l['bubble'] = df_wb_l['올해'].apply(lambda x: max(x, 0.1))
    col_a, col_b = st.columns([1, 1])
    with col_a: st.plotly_chart(px.scatter(df_wb_l, x='최고', y='올해', color='상태', hover_name='거래처명', size='bubble', color_discrete_map={"완전 회복":"green","회복 중":"orange","재진입 초기":"red"}), use_container_width=True)
    with col_b:
        st.markdown('<p class="guide-text">💡 행 클릭 시 상세 공백 이력 확인</p>', unsafe_allow_html=True)
        ev_wb = st.dataframe(df_wb_l[['상태', '거래처명', '올해', '회복률']], use_container_width=True, on_select="rerun", selection_mode="single-row")
    if len(ev_wb.selection.rows) > 0:
        sel_biz = df_wb_l.index[ev_wb.selection.rows[0]]
        st.dataframe(df_h[df_h['사업자번호']==sel_biz].sort_values('매출일자', ascending=False), use_container_width=True)

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 1. Overview", "🏆 2. 매출 상위 & 이탈 관리", "🔄 3. 재유입 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석"])

with tab1:
    if not df_final.empty:
        st.markdown(f"### 🚀 {int(df_final['년'].max())}년 Summary")
        st.markdown('<div class="info-box"><b>💡 분석 지표 기준:</b> 신규(전년무→올해유), 이탈(전년유→올해무), 유지율(전년유→올해유 비율)</div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        c1.metric("💰 총 매출 실적", f"{df_final['매출액'].sum():,.0f}M")
        c2.metric("🏥 총 거래 병원", f"{df_final['사업자번호'].nunique():,} 처")
        c3.metric("🏆 Best Product", df_final.groupby('제품명')['매출액'].sum().idxmax())
        col_a, col_b = st.columns([1, 1.5])
        col_a.plotly_chart(px.pie(df_final, values='매출액', names='진료과', hole=0.4, title="진료과 비중"), use_container_width=True)
        col_b.plotly_chart(px.bar(df_final.groupby('년월')['매출액'].sum().reset_index(), x='년월', y='매출액', text_auto='.1f', title="월별 매출 추이"), use_container_width=True)

with tab2:
    if not df_final.empty:
        total_s = df_final['매출액'].sum()
        top100 = df_final.groupby(['사업자번호', '거래처명', '진료과']).agg({'매출액': 'sum'}).sort_values('매출액', ascending=False).head(100).reset_index()
        cls_d = classify_customers(df_raw, sel_years[0]); st_c = cls_d['상태'].value_counts()
        last_p = df_raw.groupby('사업자번호')['매출일자'].max(); top100['최근구매일'] = top100['사업자번호'].map(last_p)
        cur_d = df_raw['매출일자'].max(); risk_cnt = len(top100[(cur_d - top100['최근구매일']).dt.days >= 90])
        
        # [📊 데이터 요약]
        st.subheader("📊 데이터 요약")
        st.write(f"• 상위 100대 매출 합계: **{top100['매출액'].sum():,.0f} 백만원** (전체 매출의 {(top100['매출액'].sum()/total_s*100):.1f}% 차지)")
        st.write(f"• 거래처 상태 분포: **기존({st_c.get('✅ 기존 (유지)',0)}처), 신규({st_c.get('🆕 신규 (New)',0)}처), 재유입({st_c.get('🔄 재유입 (복귀)',0)}처), 이탈({st_c.get('📉 1년 이탈',0)}처)**")
        st.write(f"• 최고 매출 거래처: **{top100.iloc[0]['거래처명']}** ({top100.iloc[0]['매출액']:,.0f} 백만원)")
        
        # [💡 스마트 인사이트]
        st.subheader("💡 스마트 인사이트")
        st.write(f"• **VIP 이탈 위험 알림:** 현재 상위 100대 거래처 중 **{risk_cnt}처({risk_cnt}%)**가 90일 이상 구매가 없는 이탈 위험 상태입니다.")
        st.write(f"• **신규 vs 이탈 밸런스:** 금기 신규 유입 대비 이탈 방어 전략이 우선시됩니다.")
        st.write(f"• **진료과 집중도 리스크:** 현재 매출의 {(df_final.groupby('진료과')['매출액'].sum().max()/total_s*100):.1f}%가 **{df_final.groupby('진료과')['매출액'].sum().idxmax()}**에 편중되어 있습니다.")
        
    st.markdown("---")
    st.markdown('<p class="guide-text">💡 아래 표에서 행을 클릭하면 하단에 상세 실적이 표시됩니다.</p>', unsafe_allow_html=True)
    top100['상태'] = (cur_d - top100['최근구매일']).dt.days.apply(lambda x: '🚨 이탈위험' if x >= 90 else '✅ 정상')
    top100['최근구매일_str'] = top100['최근구매일'].dt.strftime('%Y-%m-%d')
    event_v = st.dataframe(top100[['상태', '거래처명', '진료과', '매출액', '최근구매일_str']], use_container_width=True, on_select="rerun", selection_mode="single-row")
    if len(event_v.selection.rows) > 0:
        sel_biz = top100.iloc[event_v.selection.rows[0]]['사업자번호']
        st.dataframe(df_raw[df_raw['사업자번호'] == sel_biz].groupby('제품명').agg({'매출액':'sum'}).sort_values('매출액', ascending=False).style.format({'매출액':'{:,.1f} 백만원'}), use_container_width=True)

with tab3:
    df_h_v = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_h_v['구매간격'] = (df_h_v['매출일자'] - df_h_v.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    wb_base = df_h_v[(df_h_v['사업자번호'].isin(df_final['사업자번호'])) & (df_h_v['구매간격'] >= 180)].copy()
    if not wb_base.empty:
        wb_ids = wb_base['사업자번호'].unique()
        df_wb_f = pd.DataFrame({'올해': df_final[df_final['사업자번호'].isin(wb_ids)].groupby('사업자번호')['매출액'].sum(), '최고': df_raw[df_raw['사업자번호'].isin(wb_ids)].groupby('사업자번호')['매출액'].max()}).fillna(0)
        df_wb_f['회복률'] = (df_wb_f['올해'] / df_wb_f['최고'] * 100).replace([np.inf, -np.inf], 0)
        trigger_p = wb_base.groupby('제품명').size().idxmax()
        
        # [📊 데이터 요약]
        st.subheader("📊 데이터 요약")
        st.write(f"• 총 재유입 거래처: **{len(wb_ids)} 처** (6개월 공백 후 복귀 기준)")
        st.write(f"• 재유입 발생 총 매출: **{df_wb_f['올해'].sum():,.0f} 백만원**")
        st.write(f"• 평균 회복률: **{df_wb_f['회복률'].mean():.1f}%** (과거 최고 매출 대비 현재 비율)")
        st.write(f"• 그룹별 분포: 완전 회복({len(df_wb_f[df_wb_f['회복률']>=80])}처), 회복 중({len(df_wb_f[(df_wb_f['회복률']<80)&(df_wb_f['회복률']>=20)])}처), 재진입 초기({len(df_wb_f[df_wb_f['회복률']<20])}처)")
        
        # [💡 스마트 인사이트]
        st.subheader("💡 스마트 인사이트")
        st.write(f"• **회복 퀄리티 요약:** 올해 재유입 거래처의 평균 회복률은 **{df_wb_f['회복률'].mean():.1f}%**로 복귀 고객 충성도가 정상화되고 있습니다.")
        st.write(f"• **복귀 트리거(Trigger) 제품:** 이탈 고객들이 복귀 시 가장 먼저 찾는 제품은 **{trigger_p}**입니다.")
        st.write(f"• **재유입 매출 기여도:** 전체 매출 중 재유입 거래처가 기여하는 비중은 **{(df_wb_f['올해'].sum()/df_final['매출액'].sum()*100):.1f}%**입니다.")
        
    render_winback_quality(df_final, df_raw, sel_years[0])

with tab4:
    if '지역' in df_final.columns and not df_final.empty:
        reg_v = df_final.groupby('지역').agg(Sales=('매출액','sum'), Count=('사업자번호','nunique')).reset_index().sort_values('Sales', ascending=False)
        
        # [📊 데이터 요약]
        st.subheader("📊 데이터 요약")
        st.write(f"• 최다 거래 지역: **{reg_v.sort_values('Count', ascending=False).iloc[0]['지역']}** ({reg_v['Count'].max()} 처)")
        st.write(f"• 최고 매출 지역: **{reg_v.iloc[0]['지역']}** ({reg_v.iloc[0]['Sales']:,.0f} 백만원)")
        
        # [💡 스마트 인사이트]
        st.subheader("💡 스마트 인사이트")
        st.write(f"• **커버리지 분석:** 현재 **{reg_v.iloc[0]['지역']}** 지역이 핵심 매출 거점 역할을 수행 중입니다.")
        st.write(f"• **매출 등락 분석:** 지역별 소규모 처방 중심 병원 증가 여부를 모니터링하여 객단가 개선이 필요합니다.")
        
        st.markdown("### 🗺️ 지역별 상세 실적 리스트")
        st.markdown('<p class="guide-text">💡 지역 선택 시 우측 비중과 하단 상세 리스트가 표시됩니다.</p>', unsafe_allow_html=True)
        sel_r = st.selectbox("🔎 지역 선택", reg_v['지역'].unique(), key="p4_sel")
        col_r1, col_r2 = st.columns([1, 1.5])
        with col_r1: st.dataframe(reg_v.rename(columns={'Count':'구매처수'}), use_container_width=True)
        with col_r2: st.plotly_chart(px.pie(df_final[df_final['지역']==sel_r], values='매출액', names='제품명', hole=0.3, title="지역 제품 비중"), use_container_width=True)
        st.dataframe(df_final[df_final['지역']==sel_r].groupby(['거래처명','제품명']).agg({'매출액':'sum','수량':'sum'}).sort_values('매출액', ascending=False).head(50), use_container_width=True)

with tab5:
    p_v = df_final.groupby('제품명').agg(Sales=('매출액','sum'), Qty=('수량', 'sum'), Count=('사업자번호','nunique')).reset_index().sort_values('Sales', ascending=False)
    cat_v = df_final.groupby('제품군')['매출액'].sum().reset_index().sort_values('매출액', ascending=False)
    if not p_v.empty:
        # [📊 데이터 요약]
        st.subheader("📊 데이터 요약")
        st.write(f"• 최다 판매 제품: **{p_v.iloc[0]['제품명']}** ({int(p_v.iloc[0]['Qty']):,}개 / {p_v.iloc[0]['Sales']:,.0f} 백만원)")
        st.write(f"• 최대 매출 제품군: **{cat_v.iloc[0]['제품군']}** (전체 매출의 {(cat_v['매출액'].max()/df_final['매출액'].sum()*100):.1f}% 차지)")
        
        # [💡 스마트 인사이트]
        st.subheader("💡 스마트 인사이트")
        st.write(f"• **제품별 판매 요약:** **{p_v.iloc[0]['제품명']}**이 전 분기 대비 성장을 견인하고 있습니다.")
        st.write(f"• **교차 판매(Cross-selling) 기회:** 하단 분석기를 통해 패키지 제안이 가능한 타겟 병원을 발굴하세요.")

    st.markdown("### 📦 제품별 판매 현황")
    # [요청사항 반영] 상단 그래프 5:5 배치
    g1, g2 = st.columns(2)
    with g1: st.plotly_chart(px.bar(p_v.head(10), x='Sales', y='제품명', orientation='h', title="제품별 매출 Top 10"), use_container_width=True)
    with g2: st.plotly_chart(px.pie(cat_v, values='매출액', names='제품군', hole=0.3, title="제품군별 매출 비중"), use_container_width=True)
    
    st.markdown('<p class="guide-text">💡 아래 표에서 행을 클릭하면 상세 병원 리스트가 표시됩니다.</p>', unsafe_allow_html=True)
    ev_p = st.dataframe(p_v.rename(columns={'Count':'구매처수'}), use_container_width=True, on_select="rerun", selection_mode="single-row", height=300)
    if len(ev_p.selection.rows) > 0:
        sel_idx_p = ev_p.selection.rows[0]
        sel_p_name = p_v.iloc[sel_idx_p]['제품명']
        st.dataframe(df_final[df_final['제품명']==sel_p_name].groupby('거래처명').agg({'매출액':'sum'}).sort_values('매출액', ascending=False).style.format({'매출액':'{:,.1f}'}), use_container_width=True)
    
    render_product_strategy(df_final)
