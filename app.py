import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# --------------------------------------------------------------------------------
# 1. 페이지 설정 및 권한 제어 (URL 파라미터)
# --------------------------------------------------------------------------------
st.set_page_config(
    page_title="SKBS Sales Report",
    layout="wide",
    initial_sidebar_state="expanded"
)

# URL 파라미터 확인 (?mode=edit 인 경우에만 사이드바 노출)
query_params = st.query_params
is_edit_mode = query_params.get("mode") == "edit"

# 일반 사용자 접속 시 사이드바를 숨기는 CSS
if not is_edit_mode:
    st.markdown("""
        <style>
            [data-testid="stSidebar"] {display: none;}
            section[data-testid="stSidebar"] {width: 0px;}
        </style>
    """, unsafe_allow_html=True)

st.markdown("""
<style>
    div.block-container {padding-top: 1rem;}
    .metric-card {
        background-color: #f8f9fa;
        border-left: 5px solid #4e79a7;
        padding: 15px;
        border-radius: 5px;
        margin-bottom: 10px;
    }
    .info-box {
        padding: 15px;
        border-radius: 5px;
        font-size: 14px;
        margin-bottom: 20px;
        border: 1px solid #e0e0e0;
        background-color: #ffffff;
    }
    .guide-text {
        color: #007BFF;
        font-size: 13px;
        font-weight: bold;
        margin-bottom: 10px;
    }
</style>
""", unsafe_allow_html=True)

st.title("📊 SKBS Sales Report")

# --------------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리 (구글 드라이브 연동)
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def load_data_from_drive(file_id):
    url = f"https://drive.google.com/uc?id={'1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr
'}"
    try:
        # 대용량 처리를 위해 engine 명시
        df = pd.read_excel(url, engine='openpyxl')
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}")
        return pd.DataFrame()

    df.columns = df.columns.astype(str).str.strip()
    
    # 원본 코드의 컬럼 매핑 로직 유지
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
        
        # 제품명 정제 (괄호 제거)
        if '제품명' in df.columns:
            df['제품명'] = df['제품명'].str.replace(r'\(.*?\)', '', regex=True).str.strip()
        
        for col in ['합계금액', '수량']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        df['매출액'] = df['합계금액'] / 1000000
        
        # 판매채널 구분 (온라인/오프라인)
        def classify_channel(group):
            online_list = ['B2B', 'B2B(W)', 'SAP', '의사회원']
            return '🌐 온라인' if group in online_list else ('🏢 오프라인' if group == 'SDP' else '기타')

        if '거래처그룹' in df.columns:
            df['판매채널'] = df['거래처그룹'].apply(classify_channel)
        
        str_cols = ['거래처그룹', '제품명', '제품군', '진료과', '지역']
        for col in str_cols:
            if col in df.columns:
                df[col] = df[col].astype(str).replace('nan', '미분류')
    except Exception as e:
        st.error(f"전처리 오류: {e}")
        return pd.DataFrame()
    return df

# --------------------------------------------------------------------------------
# 3. 데이터 분석 함수 (원본 로직 복구)
# --------------------------------------------------------------------------------
@st.cache_data
def classify_customers(df, target_year):
    # 년도별 구매 여부 테이블
    cust_year = df.groupby(['사업자번호', '년']).size().unstack(fill_value=0)
    # 기초 정보 추출
    base_info = df.sort_values('매출일자').groupby('사업자번호').agg({
        '거래처명': 'last', '진료과': 'last', '지역': 'last', '매출일자': 'max'
    }).rename(columns={'매출일자': '최근구매일'})
    # 해당 년도 매출 합산
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
# 4. 필터링 및 사이드바 제어
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = '1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr'
df_raw = load_data_from_drive(DRIVE_FILE_ID)

if df_raw.empty:
    st.stop()

# 디폴트 필터값 (수정 모드가 아닐 때 적용됨)
sel_years = [df_raw['년'].max()]
sel_channels = sorted(df_raw['판매채널'].unique())
sel_quarters = sorted(df_raw['분기'].unique())
sel_months = sorted(df_raw['월'].unique())
sel_cats = sorted(df_raw['제품군'].unique())
sel_products = sorted(df_raw['제품명'].unique())

if is_edit_mode:
    with st.sidebar:
        st.header("⚙️ 관리자 설정")
        sel_channels = st.multiselect("판매채널 선택", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도 선택", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        sel_quarters = st.multiselect("분기 선택", sorted(df_raw['분기'].unique()), default=sel_quarters)
        sel_months = st.multiselect("월 선택", sorted(df_raw['월'].unique()), default=sel_months)
        sel_cats = st.multiselect("제품군 선택", sorted(df_raw['제품군'].unique()), default=sel_cats)
        
        temp_df = df_raw[df_raw['제품군'].isin(sel_cats)] if sel_cats else df_raw
        sel_products = st.multiselect("제품명 선택", sorted(temp_df['제품명'].unique()), default=sorted(temp_df['제품명'].unique()))

# 필터링 적용
df_year_filtered = df_raw[df_raw['년'].isin(sel_years)]
df_final = df_year_filtered[
    (df_year_filtered['판매채널'].isin(sel_channels)) &
    (df_year_filtered['분기'].isin(sel_quarters)) &
    (df_year_filtered['월'].isin(sel_months)) &
    (df_year_filtered['제품군'].isin(sel_cats)) &
    (df_year_filtered['제품명'].isin(sel_products))
]

# --------------------------------------------------------------------------------
# 5. 메인 탭 구성 (원본 디자인 및 로직 완벽 복구)
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "🏆 VIP & 이탈", "🔄 재유입 분석", "🗺️ 지역 분석", "📦 제품 분석"])

# --- [TAB 1] Overview ---
with tab1:
    st.markdown("### 📈 성과 요약")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 매출액 (년도)", f"{df_year_filtered['매출액'].sum():,.0f}백만원")
    c2.metric("총 구매처수 (년도)", f"{df_year_filtered['사업자번호'].nunique():,}처")
    c3.metric("분기 매출액", f"{df_final['매출액'].sum():,.1f}백만원")
    c4.metric("분기 구매처수", f"{df_final['사업자번호'].nunique():,}처")
    
    st.markdown("---")
    col_a, col_b = st.columns([1, 1.5])
    with col_a:
        st.subheader("🏥 진료과별 매출 비중")
        st.plotly_chart(px.pie(df_final, values='매출액', names='진료과', hole=0.4), use_container_width=True)
    with col_b:
        st.subheader("📅 월별 매출/처수 추이")
        monthly = df_final.groupby('년월').agg({'매출액': 'sum', '사업자번호': 'nunique'}).reset_index()
        fig = go.Figure()
        fig.add_trace(go.Bar(x=monthly['년월'], y=monthly['매출액'], name='매출(M)', yaxis='y1', marker_color='#a8dadc'))
        fig.add_trace(go.Scatter(x=monthly['년월'], y=monthly['사업자번호'], name='처수', yaxis='y2', line=dict(color='#e63946', width=3)))
        fig.update_layout(yaxis2=dict(overlaying='y', side='right'), legend=dict(orientation='h', y=1.1))
        st.plotly_chart(fig, use_container_width=True)

# --- [TAB 2] VIP & 이탈 관리 ---
with tab2:
    st.markdown("### 🏆 VIP 및 이탈 관리")
    with st.expander("🥇 매출 상위 거래처 (VIP) Top 100", expanded=True):
        st.markdown("**※ 이탈 위험군 기준:** 최근 구매일로부터 **90일(3개월)** 이상 경과 시 **'🚨 이탈위험'**, 그 미만은 **'✅ 정상'**.")
        st.markdown('<p class="guide-text">💡 <b>안내:</b> 아래 목록에서 <b>거래처 행을 선택</b>하시면 하단에서 품목별 상세 현황을 확인할 수 있습니다.</p>', unsafe_allow_html=True)
        
        ranking = df_final.groupby(['사업자번호', '거래처명', '진료과']).agg({'매출액': 'sum', '수량': 'sum'}).reset_index()
        top100 = ranking.sort_values('매출액', ascending=False).head(100).copy()
        
        last_p = df_raw.groupby('사업자번호')['매출일자'].max()
        cur_date = df_raw['매출일자'].max()
        top100['최근구매일'] = top100['사업자번호'].map(last_p)
        top100['상태'] = top100['최근구매일'].apply(lambda x: '🚨 이탈위험' if (cur_date - x).days >= 90 else '✅ 정상')
        
        sel_event = st.dataframe(top100[['상태', '거래처명', '진료과', '매출액', '수량']].style.format({'매출액': '{:,.1f}M'}), 
                                 use_container_width=True, on_select="rerun", selection_mode="single-row", height=350)

        if len(sel_event.selection.rows) > 0:
            idx = top100.index[sel_event.selection.rows[0]]
            bz_no = top100.loc[idx, '사업자번호']
            st.subheader(f"🏥 [{top100.loc[idx, '거래처명']}] 품목별 실적 합산")
            dtl = df_raw[df_raw['사업자번호'] == bz_no].groupby('제품명').agg({'수량': 'sum', '매출액': 'sum'}).reset_index()
            st.dataframe(dtl.sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f}M'}), use_container_width=True)

    st.markdown("---")
    target_yr = sel_years[0] if sel_years else df_raw['년'].max()
    st.markdown(f"**※ 거래처 상태 분류 설명 ({target_yr}년 기준)**")
    st.write("🆕 신규: 올해 첫 구매 | ✅ 기존: 작년/올해 유지 | 🔄 재유입: 이탈 후 올해 복귀 | 📉 이탈: 과거 구매 후 올해 거래 없음")
    
    cls_df = classify_customers(df_raw, target_yr)
    c_s1, c_s2 = st.columns([1, 2])
    with c_s1:
        st.markdown(f"**📊 {target_yr}년 상태 통계**")
        st.dataframe(cls_df['상태'].value_counts().reset_index().rename(columns={'count':'거래처수'}), use_container_width=True)
        sel_st = st.selectbox("👇 분석할 그룹 선택", sorted(cls_df['상태'].unique()), key="p2_sel")
    with c_s2:
        st.plotly_chart(px.pie(cls_df[cls_df['상태'] == sel_st], names='진료과', title=f"'{sel_st}' 그룹 진료과 분포"), use_container_width=True)

# --- [TAB 3] 재유입 패턴 ---
with tab3:
    st.markdown("### 🔄 재유입 패턴 및 이탈 전 분석")
    st.markdown('<p class="guide-text">💡 <b>안내:</b> 아래 제품 리스트에서 <b>행을 선택</b>하면, 해당 제품으로 복귀한 고객들의 <b>이탈 전 주요 사용 제품</b>이 우측 차트에 나타납니다.</p>', unsafe_allow_html=True)
    
    # 원본 재유입 로직 복구
    df_f = df_raw.sort_values(['사업자번호', '매출일자']).copy()
    df_f['이전_제품'] = df_f.groupby('사업자번호')['제품명'].shift(1)
    df_f['구매간격'] = (df_f['매출일자'] - df_f.groupby('사업자번호')['매출일자'].shift(1)).dt.days
    
    res = df_final.merge(df_f[['사업자번호', '매출일자', '이전_제품', '구매간격']], on=['사업자번호', '매출일자'], how='left')
    res = res[res['구매간격'] >= 90]
    
    if not res.empty:
        c_rl, c_rr = st.columns([1, 1.2])
        with c_rl:
            st.subheader("1️⃣ 재유입 제품 비중")
            res_sum = res.groupby('제품명').agg({'사업자번호': 'nunique', '매출액': 'sum'}).reset_index().rename(columns={'사업자번호': '재유입처수', '매출액': '총_매출'}).sort_values('재유입처수', ascending=False)
            ev_res = st.dataframe(res_sum.style.format({'총_매출': '{:,.1f}M'}), use_container_width=True, on_select="rerun", selection_mode="single-row", height=400)
        with c_rr:
            if len(ev_res.selection.rows) > 0:
                s_p = res_sum.iloc[ev_res.selection.rows[0]]['제품명']
                st.markdown(f"#### 🔎 [{s_p}] 고객의 이탈 전 사용 제품")
                bz_ids = res[res['제품명'] == s_p]['사업자번호'].unique()
                prev = res[res['사업자번호'].isin(bz_ids)].groupby('이전_제품').agg({'사업자번호': 'nunique'}).reset_index().rename(columns={'사업자번호': '처수'}).sort_values('처수', ascending=False).head(10)
                st.plotly_chart(px.bar(prev, x='처수', y='이전_제품', orientation='h', title="이탈 전 제품 Top 10"), use_container_width=True)
    else: st.info("재유입 데이터가 없습니다.")

# --- [TAB 4] 지역 분석 ---
with tab4:
    st.markdown("### 🗺️ 지역별 실적 현황")
    reg_s = df_final.groupby('지역').agg({'매출액': 'sum', '사업자번호': 'nunique'}).reset_index().rename(columns={'사업자번호': '구매처수'}).sort_values('매출액', ascending=False)
    reg_s['마커크기'] = reg_s['매출액'].clip(lower=0)
    
    c_r1, c_r2 = st.columns([1, 1.5])
    with c_r1:
        st.dataframe(reg_s[['지역', '매출액', '구매처수']].style.format({'매출액': '{:,.1f}M'}), use_container_width=True)
        sel_reg = st.selectbox("🔎 지역 상세 분석 선택", reg_s['지역'].unique()) if not reg_s.empty else None
    with c_r2:
        if not reg_s.empty:
            st.plotly_chart(px.scatter(reg_s, x='구매처수', y='매출액', text='지역', size='마커크기', color='매출액', title="지역별 매출/처수 분포"), use_container_width=True)
    
    if sel_reg:
        st.markdown("---")
        col_reg_a, col_reg_b = st.columns(2)
        reg_df = df_final[df_final['지역'] == sel_reg]
        with col_reg_a:
            st.subheader(f"📊 [{sel_reg}] 제품 비중")
            st.plotly_chart(px.pie(reg_df, values='매출액', names='제품명', hole=0.3), use_container_width=True)
        with col_reg_b:
            st.subheader(f"🏠 [{sel_reg}] 상위 거래처")
            r_agg = reg_df.groupby(['거래처명', '제품명']).agg({'매출액': 'sum', '수량': 'sum'}).reset_index().sort_values('매출액', ascending=False).head(30)
            st.dataframe(r_agg.style.format({'매출액': '{:,.1f}M'}), use_container_width=True)

# --- [TAB 5] 제품 분석 ---
with tab5:
    st.markdown("### 📦 제품별 판매 및 고객 리스트")
    st.markdown('<p class="guide-text">💡 <b>안내:</b> 리스트에서 <b>제품을 선택</b>하면 해당 제품을 구매한 거래처 목록이 하단에 나타납니다.</p>', unsafe_allow_html=True)
    prod_data = df_final.groupby('제품명').agg({'매출액': 'sum', '수량': 'sum', '사업자번호': 'nunique'}).reset_index().rename(columns={'사업자번호': '구매처수'}).sort_values('매출액', ascending=False)
    ev_p = st.dataframe(prod_data.style.format({'매출액': '{:,.1f}M'}), use_container_width=True, on_select="rerun", selection_mode="single-row", height=300)
    
    if len(ev_p.selection.rows) > 0:
        p_idx = prod_data.index[ev_p.selection.rows[0]]
        p_name = prod_data.loc[p_idx, '제품명']
        st.subheader(f"🎯 [{p_name}] 구매 거래처 리스트")
        p_dtl = df_final[df_final['제품명'] == p_name].groupby('거래처명').agg({'매출액': 'sum', '수량': 'sum'}).reset_index()
        st.dataframe(p_dtl.sort_values('매출액', ascending=False).style.format({'매출액': '{:,.1f}M'}), use_container_width=True)
