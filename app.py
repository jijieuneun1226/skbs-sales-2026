import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# --------------------------------------------------------------------------------
# 1. 페이지 설정 및 권한 제어
# --------------------------------------------------------------------------------
st.set_page_config(page_title="SKBS Sales Report", layout="wide", initial_sidebar_state="expanded")

query_params = st.query_params
is_edit_mode = query_params.get("mode") == "edit"

if not is_edit_mode:
    st.markdown("<style>[data-testid='stSidebar'] {display: none;} section[data-testid='stSidebar'] {width: 0px;}</style>", unsafe_allow_html=True)

st.markdown("""
<style>
    div.block-container {padding-top: 1rem;}
    .metric-card {background-color: #f8f9fa; border-left: 5px solid #4e79a7; padding: 15px; border-radius: 5px; margin-bottom: 10px;}
    .guide-text {color: #007BFF; font-size: 13px; font-weight: bold; margin-bottom: 10px;}
</style>
""", unsafe_allow_html=True)

st.title("📊 SKBS Sales Report")

# --------------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def load_data_from_drive(file_id):
    url = f"https://drive.google.com/uc?id={file_id}"
    try:
        df = pd.read_excel(url, engine='openpyxl')
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}")
        return pd.DataFrame()

    df.columns = df.columns.astype(str).str.strip()
    col_map = {
        '매출일자': ['매출일자', '날짜', 'Date', '일자'],
        '제품명': ['제품명 변환', '제품명변환', '제품명', '품목명'],
        '합계금액': ['합계금액', '매출액', '금액'],
        '수량': ['수량', '판매수량'],
        '사업자번호': ['사업자번호', '사업자등록번호'],
        '거래처명': ['거래처명', '병원명'],
        '진료과': ['진료과', '진료과목'],
        '제품군': ['제품군', '카테고리'],
        '거래처그룹': ['거래처그룹', '그룹'],
        '지역': ['지역']
    }
    
    current_cols = {c.replace(' ', ''): c for c in df.columns}
    for std_col, candidates in col_map.items():
        if std_col in df.columns: continue
        for cand in candidates:
            clean_cand = cand.replace(' ', '')
            for clean_real, real in current_cols.items():
                if clean_real == clean_cand:
                    df.rename(columns={real: std_col}, inplace=True)
                    break
            if std_col in df.columns: break

    try:
        df['매출일자'] = pd.to_datetime(df['매출일자'])
        df = df.sort_values('매출일자')
        df['년'] = df['매출일자'].dt.year
        df['분기'] = df['매출일자'].dt.quarter
        df['월'] = df['매출일자'].dt.month
        df['년월'] = df['매출일자'].dt.strftime('%Y-%m')
        
        if '제품명' in df.columns:
            df['제품명'] = df['제품명'].str.replace(r'\(.*?\)', '', regex=True).str.strip()
        
        for col in ['합계금액', '수량']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        # 매출액 단위: 백만원 통일
        df['매출액_백만'] = df['합계금액'] / 1000000
        
        def classify_channel(group):
            online_list = ['B2B', 'B2B(W)', 'SAP', '의사회원']
            return '🌐 온라인' if group in online_list else ('🏢 오프라인' if group == 'SDP' else '기타')

        if '거래처그룹' in df.columns:
            df['판매채널'] = df['거래처그룹'].apply(classify_channel)
    except Exception as e:
        st.error(f"전처리 오류: {e}")
        return pd.DataFrame()
    return df

@st.cache_data
def classify_customers(df, target_year):
    cust_year = df.groupby(['사업자번호', '년']).size().unstack(fill_value=0)
    base_info = df.sort_values('매출일자').groupby('사업자번호').agg({
        '거래처명': 'last', '진료과': 'last', '지역': 'last', '매출일자': 'max'
    }).rename(columns={'매출일자': '최근구매일'})
    sales_ty = df[df['년'] == target_year].groupby('사업자번호')['매출액_백만'].sum()
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
            if has_t1: status = "📉 이탈 (최근)"
            else: status = "💤 장기 이탈"
        classification[biz_no] = status
    base_info['상태'] = base_info.index.map(classification)
    return base_info

# --------------------------------------------------------------------------------
# 3. 사이드바 필터 (분기-월 연동 포함)
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = '1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr'
df_raw = load_data_from_drive(DRIVE_FILE_ID)
if df_raw.empty: st.stop()

# 디폴트 설정
sel_years = [df_raw['년'].max()]
sel_channels = sorted(df_raw['판매채널'].unique())
sel_quarters = sorted(df_raw['분기'].unique())
sel_cats = sorted(df_raw['제품군'].unique())

if is_edit_mode:
    with st.sidebar:
        st.header("⚙️ 관리자 설정")
        sel_channels = st.multiselect("판매채널 선택", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도 선택", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        sel_quarters = st.multiselect("분기 선택", sorted(df_raw['분기'].unique()), default=sel_quarters)
        
        # 분기-월 연동 로직
        q_to_m = {1: [1,2,3], 2: [4,5,6], 3: [7,8,9], 4: [10,11,12]}
        avail_months = []
        for q in sel_quarters: avail_months.extend(q_to_m[q])
        sel_months = st.multiselect("월 선택", sorted(avail_months), default=sorted(avail_months))
        
        sel_cats = st.multiselect("제품군 선택", sorted(df_raw['제품군'].unique()), default=sel_cats)
        temp_df = df_raw[df_raw['제품군'].isin(sel_cats)]
        sel_products = st.multiselect("제품명 선택", sorted(temp_df['제품명'].unique()), default=sorted(temp_df['제품명'].unique()))
else:
    # 일반 모드 시 자동 월 설정
    q_to_m = {1: [1,2,3], 2: [4,5,6], 3: [7,8,9], 4: [10,11,12]}
    sel_months = []
    for q in sel_quarters: sel_months.extend(q_to_m[q])
    sel_products = sorted(df_raw['제품명'].unique())

df_final = df_raw[
    (df_raw['년'].isin(sel_years)) & (df_raw['판매채널'].isin(sel_channels)) &
    (df_raw['분기'].isin(sel_quarters)) & (df_raw['월'].isin(sel_months)) &
    (df_raw['제품군'].isin(sel_cats)) & (df_raw['제품명'].isin(sel_products))
]

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 1. Overview", "🏆 2. VIP & 이탈 관리", "🔄 3. 재유입 패턴 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석"])

# --- [TAB 1] Overview ---
with tab1:
    st.markdown("### 📈 성과 요약")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 매출액 (년도)", f"{df_year_filtered['매출액'].sum():,.0f}백만원")
    c2.metric("총 구매처수 (년도)", f"{df_year_filtered['사업자번호'].nunique():,}처")
    c3.metric("분기 매출액", f"{df_final['매출액'].sum():,.0f}백만원")
    c4.metric("분기 구매처수", f"{df_final['사업자번호'].nunique():,}처")
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
        fig_dual.update_layout(yaxis=dict(side='left'), yaxis2=dict(side='right', overlaying='y'), legend=dict(x=0, y=1.1, orientation='h'))
        st.plotly_chart(fig_dual, use_container_width=True)

# --- [TAB 2] VIP & 이탈 관리 ---
with tab2:
    st.markdown("### 🏆 VIP 관리 및 거래처 분류 상세 분석")
    with st.expander("🥇 매출 상위 거래처 (VIP) Top 100", expanded=True):
        st.markdown("**※ 이탈 위험군 기준:** 최근 구매일로부터 **90일(3개월)** 이상 경과 시 **'🚨 이탈위험'**, 그 미만은 **'✅ 정상'**.")
        st.info("💡 **표에서 행을 클릭**하면 하단에 상세 품목별 실적이 합산되어 표시됩니다.")
        if not df_final.empty:
            ranking = df_final.groupby(['사업자번호', '거래처명', '진료과']).agg({'매출액': 'sum', '수량': 'sum'}).reset_index()
            top100 = ranking.sort_values('매출액', ascending=False).head(100).copy()
            top100['객단가'] = (top100['매출액'] * 1000000 / top100['수량']).round(0)
            cur_date = df_raw['매출일자'].max()
            last_p = df_raw.groupby('사업자번호')['매출일자'].max()
            top100['최근구매일'] = top100['사업자번호'].map(last_p)
            top100['상태'] = top100['최근구매일'].apply(lambda x: '🚨 이탈위험' if (cur_date - x).days >= 90 else '✅ 정상')
            
            styled_vip = top100[['상태', '거래처명', '진료과', '매출액', '수량', '객단가']].style.format({'매출액': '{:,.1f}백만원', '객단가': '{:,.0f}원'})\
                .applymap(lambda v: 'color: red; font-weight: bold;' if '이탈' in str(v) else ('color: green; font-weight: bold;' if '정상' in str(v) else ''), subset=['상태'])
            event_vip = st.dataframe(styled_vip, use_container_width=True, on_select="rerun", selection_mode="single-row", height=350)
            
            if len(event_vip.selection.rows) > 0:
                v_idx = top100.index[event_vip.selection.rows[0]]
                st.subheader(f"🏥 [{top100.loc[v_idx, '거래처명']}] 품목별 실적 합산")
                v_detail = df_raw[df_raw['사업자번호'] == top100.loc[v_idx, '사업자번호']].groupby('제품명').agg({'수량': 'sum', '매출액': 'sum'}).reset_index()
                v_detail['객단가'] = (v_detail['매출액'] * 1000000 / v_detail['수량']).round(0)
                st.dataframe(v_detail.sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f}백만원', '객단가': '{:,.0f}원'}), use_container_width=True)

    st.markdown("---")
    target_yr = sel_years[0] if sel_years else df_raw['년'].max()
    st.markdown(f"""<div class="info-box"><b>※ 거래처 상태 분류 설명 ({target_yr}년 기준)</b><br>
    🆕 신규: {target_yr}년 최초 구매 / ✅ 기존: {target_yr}년과 전년도 모두 구매 / 🔄 재유입: 전년도는 쉬었으나 올해 복귀<br>
    📉 이탈: 해당 기간 이후 구매 없음 (1년: 작년만 구매, 2~3년: 이전 구매 후 공백기)</div>""", unsafe_allow_html=True)
    
    cls_df = classify_customers(df_raw, target_yr)
    # [중요] 분류 분석 시에도 거래처그룹 필터 적용
    if sel_groups:
        biz_in_group = df_raw[df_raw['거래처그룹'].isin(sel_groups)]['사업자번호'].unique()
        cls_df = cls_df[cls_df.index.isin(biz_in_group)]

    c_s1, c_s2 = st.columns([1, 2])
    with c_s1:
        st.markdown(f"**📊 {target_yr}년 상태 통계**")
        st.dataframe(cls_df['상태'].value_counts().reset_index().rename(columns={'count':'거래처수'}), use_container_width=True)
        sel_st = st.selectbox("👇 분석할 그룹 선택", sorted(cls_df['상태'].unique()), key="p2_sel")
    with c_s2:
        st.plotly_chart(px.pie(cls_df[cls_df['상태'] == sel_st], names='진료과', title=f"'{sel_st}' 그룹 진료과 분포"), use_container_width=True)

    event_cls = st.dataframe(cls_df[cls_df['상태'] == sel_st].sort_values('해당년도_매출', ascending=False)[['거래처명', '진료과', '최근구매일', '해당년도_매출']].style.format({'해당년도_매출': '{:,.1f}백만원', '최근구매일': '{:%Y-%m-%d}'}), use_container_width=True, height=400, on_select="rerun", selection_mode="single-row")
    if len(event_cls.selection.rows) > 0:
        row_idx = cls_df[cls_df['상태'] == sel_st].sort_values('해당년도_매출', ascending=False).index[event_cls.selection.rows[0]]
        st.markdown(f"**🏥 상세 구매 이력 (최근 20건)**")
        hist = df_raw[df_raw['사업자번호'] == row_idx].sort_values('매출일자', ascending=False).head(20)
        st.dataframe(hist[['매출일자', '제품명', '매출액', '수량']].style.format({'매출액': '{:,.1f}백만원', '매출일자': '{:%Y-%m-%d}'}), use_container_width=True)

# --- [TAB 3] 재유입 패턴 ---
with tab3:
    st.markdown("### 🔄 재유입 제품별 실적 및 이탈 전 패턴")
    st.markdown("**※ 재유입 기준:** 직전 구매일로부터 **90일(3개월)** 이상의 공백기 이후 다시 구매가 발생한 경우를 의미합니다.")
    
    df_f = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_f['이전_제품'] = df_f.groupby('사업자번호')['제품명'].shift(1)
    df_f['구매간격'] = (df_f['매출일자'] - df_f.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    
    res = df_final.merge(df_f[['사업자번호', '매출일자', '이전_제품', '구매간격']], on=['사업자번호', '매출일자'], how='left')
    res = res[res['구매간격'] >= 90]
    
    if not res.empty:
        st.subheader("1️⃣ 재유입 제품 비중")
        st.plotly_chart(px.pie(res['제품명'].value_counts().reset_index(), values='count', names='제품명', hole=0.3), use_container_width=True)
        
        st.subheader("2️⃣ 제품별 재유입 상세 현황")
        st.info("💡 **아래 리스트에서 제품명을 클릭**하면, 해당 제품으로 돌아온 고객들의 **복귀 전 구매 패턴**이 우측에 나타납니다.")
        res_sum = res.groupby('제품명').agg({'사업자번호': 'nunique', '매출액': 'sum'}).reset_index().rename(columns={'사업자번호': '재유입처수', '매출액': '총_재유입매출'}).sort_values('재유입처수', ascending=False)
        c_rl, c_rr = st.columns([1, 1.2])
        with c_rl:
            ev_res = st.dataframe(res_sum.style.format({'총_재유입매출': '{:,.1f}백만원'}), use_container_width=True, on_select="rerun", selection_mode="single-row", height=400)
        with c_rr:
            if len(ev_res.selection.rows) > 0:
                s_p = res_sum.iloc[ev_res.selection.rows[0]]['제품명']
                st.markdown(f"#### 🔎 [{s_p}] 고객들의 이탈 전 제품 비중")
                bz = res[res['제품명'] == s_p]['사업자번호'].unique()
                prev = res[res['사업자번호'].isin(bz)].groupby('이전_제품').agg({'사업자번호': 'nunique'}).reset_index().rename(columns={'사업자번호': '구매처수'}).sort_values('구매처수', ascending=False).head(10)
                st.plotly_chart(px.bar(prev, x='구매처수', y='이전_제품', orientation='h', title="이탈 전 주요 사용 제품 Top 10"), use_container_width=True)
    else: st.info("선택한 조건에서 재유입 데이터가 없습니다.")

# --- [TAB 4] 지역 분석 ---
with tab4:
    st.markdown("### 🗺️ 지역별 실적 및 심층 내역")
    reg_s = df_final.groupby('지역').agg({'매출액': 'sum', '사업자번호': 'nunique'}).reset_index().rename(columns={'사업자번호': '구매처수'}).sort_values('매출액', ascending=False)
    reg_s['마커크기'] = reg_s['매출액'].clip(lower=0)
    c_r1, c_r2 = st.columns([1, 1.5])
    with c_r1:
        st.dataframe(reg_s[['지역', '매출액', '구매처수']].style.format({'매출액': '{:,.0f}백만원'}), use_container_width=True)
        if not reg_s.empty:
            sel_reg = st.selectbox("🔎 분석할 지역 선택", reg_s['지역'].unique(), key="p4_reg")
        else: sel_reg = None
    with c_r2:
        st.plotly_chart(px.scatter(reg_s, x='구매처수', y='매출액', text='지역', size='마커크기', color='매출액'), use_container_width=True)
    
    if sel_reg:
        st.markdown("---")
        col_reg_a, col_reg_b = st.columns(2)
        reg_df = df_final[df_final['지역'] == sel_reg]
        with col_reg_a:
            st.subheader(f"📊 [{sel_reg}] 구매 제품 비중")
            st.plotly_chart(px.pie(reg_df, values='매출액', names='제품명', hole=0.3), use_container_width=True)
        with col_reg_b:
            st.subheader(f"🏠 [{sel_reg}] 상위 매출 거래처 (합산)")
            r_agg = reg_df.groupby(['거래처명', '제품명']).agg({'매출액': 'sum', '수량': 'sum'}).reset_index().sort_values('매출액', ascending=False).head(50)
            r_agg['객단가'] = (r_agg['매출액'] * 1000000 / r_agg['수량']).round(0)
            st.dataframe(r_agg.style.format({'매출액': '{:,.1f}백만원', '객단가': '{:,.0f}원'}), use_container_width=True)

# --- [TAB 5] 제품 분석 ---
with tab5:
    st.markdown("### 📦 제품별 판매 현황 및 고객 상세 분석")
    st.info("💡 **리스트에서 제품을 클릭**하면 하단에 해당 제품을 구매한 거래처 상세 리스트가 나타납니다.")
    p_main = df_final.groupby('제품명').agg({'수량': 'sum', '매출액': 'sum', '사업자번호': 'nunique'}).reset_index().rename(columns={'사업자번호': '구매처수'}).sort_values('매출액', ascending=False)
    ev_p = st.dataframe(p_main.style.format({'매출액': '{:,.1f}백만원', '수량': '{:,.0f}'}), use_container_width=True, on_select="rerun", selection_mode="single-row", height=300)
    
    if len(ev_p.selection.rows) > 0:
        sel_p_name = p_main.iloc[ev_p.selection.rows[0]]['제품명']
        st.markdown("---")
        st.subheader(f"🎯 [{sel_p_name}] 구매 거래처 리스트")
        p_detail = df_final[df_final['제품명'] == sel_p_name].groupby('거래처명').agg({'수량': 'sum', '매출액': 'sum'}).reset_index()
        p_detail['객단가'] = (p_detail['매출액'] * 1000000 / p_detail['수량']).round(0)
        st.dataframe(p_detail.sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f}백만원', '객단가': '{:,.0f}원'}), use_container_width=True)
    
    st.markdown("---")
    st.subheader("📅 월별 매출 트렌드 (Top 5 제품)")
    t5_list = p_main.head(5)['제품명'].tolist()
    if t5_list:
        tr_df = df_final[df_final['제품명'].isin(t5_list)].groupby(['년월', '제품명'])['매출액'].sum().reset_index()
        st.plotly_chart(px.line(tr_df, x='년월', y='매출액', color='제품명'), use_container_width=True)

