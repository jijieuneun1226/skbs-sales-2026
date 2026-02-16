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

# URL 파라미터 읽기
params = st.query_params
is_edit_mode = params.get("mode") == "edit"

# 관리자 모드가 아닐 때만 사이드바를 숨김
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

# --------------------------------------------------------------------------------
# 2. 데이터 로드 (HTML 파싱 & 강제 다운로드 - 최종 해결 버전)
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def load_data_from_drive(file_id):
    # 1. 초기 다운로드 URL
    initial_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    session = requests.Session()

    try:
        # [1단계] 접속 시도
        response = session.get(initial_url, stream=True)
        
        # [2단계] HTML(경고창)인지 검사
        if "text/html" in response.headers.get("Content-Type", "").lower():
            html_content = response.text
            
            # (1) 경고창 내부의 '진짜 다운로드 주소(action)' 찾기
            match_action = re.search(r'action="([^"]+)"', html_content)
            
            # (2) 필요한 파라미터(confirm, uuid 등) 긁어오기
            inputs = re.findall(r'name="([^"]+)"\s+value="([^"]+)"', html_content)
            params_dict = {name: value for name, value in inputs}
            
            if match_action:
                real_download_url = match_action.group(1).replace("&amp;", "&")
                response = session.get(real_download_url, params=params_dict, stream=True)
            else:
                # 폼을 못 찾았으면 기존 방식(쿠키) 시도
                token = None
                for key, value in response.cookies.items():
                    if key.startswith('download_warning'):
                        token = value
                        break
                if token:
                    response = session.get(initial_url, params={'confirm': token}, stream=True)

        if response.status_code != 200:
            st.error(f"❌ 다운로드 연결 실패 (Code: {response.status_code})")
            return pd.DataFrame()

        # [3단계] 엑셀 열기 & 헤더 정밀 탐색
        file_bytes = io.BytesIO(response.content)
        
        try:
            df_preview = pd.read_excel(file_bytes, header=None, nrows=50, engine='openpyxl')
            target_keyword = "매출일자"
            header_row_index = -1
            
            for idx, row in df_preview.iterrows():
                row_str = row.astype(str).str.replace(r'\s+', '', regex=True).values
                if any(target_keyword in str(x) for x in row_str):
                    header_row_index = idx
                    break
            
            file_bytes.seek(0)
            if header_row_index != -1:
                df = pd.read_excel(file_bytes, header=header_row_index, engine='openpyxl')
            else:
                st.warning("⚠️ '매출일자'를 못 찾아서 2번째 줄(Index 1)을 읽습니다.")
                df = pd.read_excel(file_bytes, header=1, engine='openpyxl')

        except Exception as e:
            if b"<!DOCTYPE html>" in response.content[:200]:
                st.error("🚨 구글 보안 경고 우회 실패. 파일 '사본 만들기' 후 새 링크를 사용하세요.")
            else:
                st.error(f"❌ 엑셀 읽기 오류: {e}")
            return pd.DataFrame()

    except Exception as e:
        st.error(f"❌ 시스템 오류: {e}")
        return pd.DataFrame()

    # [4단계] 전처리
    df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
    
    col_map = {
        '매출일자': ['매출일자', '날짜', 'Date', '일자'],
        '제품명': ['제품명변환', '제품명', '품목명', 'ItemName', '제 품 명'],
        '합계금액': ['합계금액', '공급가액', '금액', '매출액', 'Amount'],
        '수량': ['수량', 'Qty', '판매수량', '수 량'],
        '사업자번호': ['사업자번호', '사업자등록번호', 'BizNo'],
        '거래처명': ['거래처명', '병원명', '요양기관명'],
        '진료과': ['진료과', '진료과목'],
        '제품군': ['제품군', '카테고리'],
        '거래처그룹': ['거래처그룹', '그룹', '판매채널'],
        '주소': ['도로명주소', '주소', '사업장주소'],
        '지역': ['지역', '시도']
    }
    
    current_cols = df.columns
    for std_col, candidates in col_map.items():
        if std_col in df.columns: continue
        for cand in candidates:
            clean_cand = re.sub(r'\s+', '', cand)
            if clean_cand in current_cols:
                df.rename(columns={clean_cand: std_col}, inplace=True)
                break
            if std_col in df.columns: break

    try:
        # 지역 생성
        if '지역' not in df.columns and '주소' in df.columns:
            df['지역_임시'] = df['주소'].astype(str).str.split().str[0]
            df['지역'] = df['지역_임시']
            df.drop(columns=['지역_임시'], inplace=True, errors='ignore')
        elif '지역' not in df.columns:
             df['지역'] = '미분류'

        # 날짜 변환
        if '매출일자' in df.columns:
            df['매출일자'] = pd.to_datetime(df['매출일자'], errors='coerce')
            df = df.dropna(subset=['매출일자'])
            df = df.sort_values('매출일자')
            df['년'] = df['매출일자'].dt.year
            df['분기'] = df['매출일자'].dt.quarter
            df['월'] = df['매출일자'].dt.month
            df['년월'] = df['매출일자'].dt.strftime('%Y-%m')
        
        # 제품명 정리
        if '제품명' in df.columns:
            df['제품명'] = df['제품명'].astype(str).str.replace(r'\(.*?\)', '', regex=True).str.strip()
        else: df['제품명'] = '미분류'
            
        # 숫자 변환
        for col in ['합계금액', '수량']:
            if col not in df.columns: df[col] = 0
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        df['매출액'] = df['합계금액'] / 1000000
        
        # 채널 분류
        def classify_channel(group):
            online_list = ['B2B', 'B2B(W)', 'SAP', '의사회원']
            return 'online'if group in online_list else ('offline' if group == 'SDP' else '기타')
            
        if '거래처그룹' in df.columns:
            df['판매채널'] = df['거래처그룹'].apply(classify_channel)
        else: df['판매채널'] = '기타'
        
        str_cols = ['거래처명', '거래처그룹', '제품군', '진료과', '지역']
        for col in str_cols:
            if col not in df.columns: df[col] = '미분류'
            df[col] = df[col].astype(str).replace('nan', '미분류')
            
        if '사업자번호' not in df.columns: df['사업자번호'] = df['거래처명']
             
    except Exception as e:
        st.error(f"❌ 전처리 오류: {e}")
        return pd.DataFrame()
        
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
# 🌟 [신규 기능 1] Tab 1: 스마트 오버뷰 (YoY, Sparkline)
# --------------------------------------------------------------------------------
def render_smart_overview(df_curr, df_raw):
    if df_curr.empty: return

    # 1. 현재 선택된 기준 연도와 월 파악
    current_year = int(df_curr['년'].max())
    last_year = current_year - 1
    selected_months = df_curr['월'].unique()

    # 2. [전년 동기 데이터] 가져오기 (비교군)
    df_prev = df_raw[
        (df_raw['년'] == last_year) & 
        (df_raw['월'].isin(selected_months))
    ]

    # 3. KPI 계산
    sales_curr = df_curr['매출액'].sum()
    sales_prev = df_prev['매출액'].sum() if not df_prev.empty else 0
    sales_pct = ((sales_curr - sales_prev) / sales_prev * 100) if sales_prev > 0 else 0

    cust_curr = set(df_curr['사업자번호'])
    cust_prev = set(df_prev['사업자번호'])
    total_cust = len(cust_curr)
    
    new_cust = len(cust_curr - cust_prev) 
    lost_cust = len(cust_prev - cust_curr)
    retained_cust = len(cust_curr & cust_prev)

    st.markdown(f"### 🚀 {current_year}년 Executive Summary (vs {last_year})")
    
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 1, 1.2])

        # [좌측] 매출 실적 + YoY
        with c1:
            st.metric(
                label="💰 총 매출 실적",
                value=f"{sales_curr:,.0f} M",
                delta=f"{sales_pct:+.1f}% (전년 동기 대비)",
                delta_color="normal"
            )
            chart_data = df_curr.groupby('월')['매출액'].sum()
            st.area_chart(chart_data, height=50, color="#FF4B4B") 
            st.caption("👆 선택 기간 월별 매출 흐름")

        # [중앙] 영업 활동성
        with c2:
            st.metric(label="🏥 총 거래 병원", value=f"{total_cust} 곳")
            st.markdown(f"""
            - **✨ 신규(New):** <span style='color:blue'>+{new_cust}</span> 곳
            - **💔 이탈(Lost):** <span style='color:red'>-{lost_cust}</span> 곳
            """, unsafe_allow_html=True)
            if total_cust > 0:
                retention_rate = (retained_cust / total_cust) * 100
                st.progress(retention_rate / 100, text=f"고객 유지율 {retention_rate:.1f}%")

        # [우측] Best Product & Risk
        with c3:
            top_prod = df_curr.groupby('제품명')['매출액'].sum().idxmax()
            top_prod_sales = df_curr.groupby('제품명')['매출액'].sum().max()
            
            st.metric(label="🏆 Best Product", value=top_prod)
            st.write(f"매출 기여: **{top_prod_sales:,.0f}M**")
            
            if lost_cust > new_cust:
                st.error(f"🚨 경고: 신규 유입보다 이탈이 **{lost_cust - new_cust}곳** 더 많습니다!")
            else:
                st.success(f"✅ 양호: 순증가 **+{new_cust - lost_cust}곳** 달성 중!")

# --------------------------------------------------------------------------------
# 🌟 [신규 기능 2] Tab 3: Win-back 퀄리티 분석
# --------------------------------------------------------------------------------
def render_winback_quality(df, current_year):
    last_year = current_year - 1
    
    # 데이터 준비
    sales_curr = df[df['년'] == current_year].groupby(['거래처명', '지역'])['매출액'].sum()
    sales_prev = df[df['년'] == last_year].groupby(['거래처명', '지역'])['매출액'].sum()
    sales_history = df[df['년'] < current_year].groupby(['거래처명', '지역'])['매출액'].max()
    
    # 재유입 정의
    current_buyers = sales_curr.index
    last_year_buyers = sales_prev.index
    historical_buyers = sales_history.index
    
    winback_list = (current_buyers.difference(last_year_buyers)).intersection(historical_buyers)
    
    if len(winback_list) == 0:
        st.info("♻️ 이번 기간에 재유입된 거래처가 없습니다.")
        return

    df_winback = pd.DataFrame(index=winback_list)
    df_winback['올해매출'] = sales_curr[winback_list]
    df_winback['과거최고매출'] = sales_history[winback_list]
    df_winback['회복률(%)'] = (df_winback['올해매출'] / df_winback['과거최고매출'] * 100).fillna(0)
    
    def get_status(rate):
        if rate >= 80: return "🟢 완전 회복"
        elif rate >= 20: return "🟡 회복 중"
        else: return "🔴 간 보기 (Test)"
        
    df_winback['상태'] = df_winback['회복률(%)'].apply(get_status)
    df_winback = df_winback.sort_values('올해매출', ascending=False).reset_index()

    st.markdown(f"### ♻️ {current_year}년 재유입(Win-back) 현황")
    
    total_recovered = df_winback['올해매출'].sum()
    count_recovered = len(df_winback)
    avg_recovery = df_winback['회복률(%)'].mean()
    
    c1, c2, c3 = st.columns(3)
    c1.metric("돌아온 거래처", f"{count_recovered}곳")
    c2.metric("확보된 매출", f"{total_recovered:,.0f}M", "이탈했다가 돌아온 금액")
    c3.metric("평균 회복률", f"{avg_recovery:.1f}%", "전성기 대비 현재 수준")

    st.markdown("---")

    col_chart, col_list = st.columns([1, 1])
    
    with col_chart:
        st.markdown("#### 📈 회복 퀄리티 매트릭스")
        st.caption("점이 위쪽에 있을수록 예전보다 더 많이 사준다는 뜻입니다.")
        
        fig = px.scatter(
            df_winback,
            x='과거최고매출',
            y='올해매출',
            color='상태',
            hover_name='거래처명',
            size='올해매출',
            color_discrete_map={"🟢 완전 회복": "green", "🟡 회복 중": "orange", "🔴 간 보기 (Test)": "red"},
            title="과거 매출(X) vs 현재 매출(Y)"
        )
        fig.add_shape(type="line", x0=0, y0=0, x1=df_winback['과거최고매출'].max(), y1=df_winback['과거최고매출'].max(),
                      line=dict(color="gray", dash="dash"))
        st.plotly_chart(fig, use_container_width=True)

    with col_list:
        st.markdown("#### 📋 상세 리스트 (집중 케어 대상)")
        display_df = df_winback[['상태', '거래처명', '올해매출', '회복률(%)']]
        
        st.dataframe(
            display_df,
            column_config={
                "회복률(%)": st.column_config.ProgressColumn(
                    "전성기 대비 회복률",
                    format="%.1f%%",
                    min_value=0,
                    max_value=100,
                ),
                "올해매출": st.column_config.NumberColumn(format="%d M")
            },
            hide_index=True,
            use_container_width=True
        )
        
        n_red = len(df_winback[df_winback['상태'].str.contains("간 보기")])
        if n_red > 0:
            st.warning(f"🚨 **Action:** '간 보기(🔴)' 상태인 **{n_red}곳**은 아직 물량을 다 주지 않았습니다. 경쟁사 제품 재고가 남았는지 확인하세요.")

# --------------------------------------------------------------------------------
# 🌟 [신규 기능 3] Tab 4: 지역별 심층 분석
# --------------------------------------------------------------------------------
def render_regional_deep_dive(df):
    if df.empty: return

    reg_stats = df.groupby('지역').agg(
        Total_Sales=('매출액', 'sum'),
        Account_Count=('사업자번호', 'nunique')
    )
    reg_stats['Per_Account'] = reg_stats['Total_Sales'] / reg_stats['Account_Count']
    reg_stats = reg_stats.reset_index()

    avg_count = reg_stats['Account_Count'].mean()
    avg_sales = reg_stats['Per_Account'].mean()

    st.markdown("### 🗺️ 지역별 심층 효율성 분석")
    st.markdown("#### 1️⃣ 영업 효율성 매트릭스 (양 vs 질)")
    st.caption("💡 오른쪽 위에 있을수록 거래처도 많고 돈도 잘 버는 '효자 지역'입니다. 왼쪽 위는 '알짜배기'입니다.")
    
    fig = px.scatter(
        reg_stats,
        x='Account_Count',
        y='Per_Account',
        size='Total_Sales',
        color='지역',
        hover_name='지역',
        text='지역',
        title="거래처 수(X) vs 객단가(Y) 매트릭스",
        labels={'Account_Count': '거래처 수 (Coverage)', 'Per_Account': '병원 1곳당 평균 매출 (Efficiency)'}
    )
    fig.add_hline(y=avg_sales, line_dash="dash", line_color="gray", annotation_text="평균 객단가")
    fig.add_vline(x=avg_count, line_dash="dash", line_color="gray", annotation_text="평균 거래처수")
    fig.update_traces(textposition='top center')
    st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns(2)
    
    with c1:
        st.markdown("#### 2️⃣ 지역별 제품 선호도 (Heatmap)")
        st.caption("특정 지역에서 유독 잘 팔리거나 안 팔리는 제품을 찾아보세요.")
        heatmap_data = df.pivot_table(index='지역', columns='제품명', values='매출액', aggfunc='sum', fill_value=0)
        heatmap_norm = heatmap_data.div(heatmap_data.sum(axis=1), axis=0) * 100
        
        fig_heat = px.imshow(
            heatmap_norm,
            labels=dict(x="제품", y="지역", color="매출 비중(%)"),
            x=heatmap_norm.columns,
            y=heatmap_norm.index,
            color_continuous_scale="Blues",
            aspect="auto"
        )
        st.plotly_chart(fig_heat, use_container_width=True)

    with c2:
        st.markdown("#### 3️⃣ '골목대장' 리스크 분석")
        risk_data = []
        for region in df['지역'].unique():
            region_df = df[df['지역'] == region]
            total = region_df['매출액'].sum()
            top_hospital = region_df.groupby('거래처명')['매출액'].sum().sort_values(ascending=False)
            if not top_hospital.empty:
                top_name = top_hospital.index[0]
                top_val = top_hospital.iloc[0]
                share = (top_val / total) * 100
                risk_data.append({'지역': region, '1등병원': top_name, '의존도': share})
        
        df_risk = pd.DataFrame(risk_data).sort_values('의존도', ascending=False)
        fig_risk = px.bar(
            df_risk,
            x='의존도',
            y='지역',
            orientation='h',
            text='의존도',
            color='의존도',
            color_continuous_scale='Reds',
            hover_data=['1등병원'],
            title="지역 내 1위 거래처 의존도 (%)"
        )
        fig_risk.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
        st.plotly_chart(fig_risk, use_container_width=True)
        
        high_risk_region = df_risk.iloc[0]
        if high_risk_region['의존도'] > 50:
            st.warning(f"🚨 **주의:** **{high_risk_region['지역']}** 지역은 매출의 **{high_risk_region['의존도']:.1f}%**가 **'{high_risk_region['1등병원']}'** 한 곳에서 나옵니다.")

# --------------------------------------------------------------------------------
# 🌟 [신규 기능 4] Tab 5: 제품 전략 심층 분석
# --------------------------------------------------------------------------------
def render_product_strategy(df):
    if df.empty: return

    st.markdown("### 💊 제품별 전략 심층 분석 (Strategy Deep Dive)")
    
    product_stats = df.groupby('제품명').agg(
        Total_Sales=('매출액', 'sum'),
        Total_Qty=('수량', 'sum'),
        Account_Count=('사업자번호', 'nunique')
    )
    
    monthly_sales = df.groupby(['제품명', '월'])['매출액'].sum().unstack(fill_value=0)
    if monthly_sales.shape[1] >= 2:
        first_month = monthly_sales.iloc[:, 0]
        last_month = monthly_sales.iloc[:, -1]
        product_stats['Growth_Rate'] = ((last_month - first_month) / first_month.replace(0, 1) * 100)
    else:
        product_stats['Growth_Rate'] = 0

    product_stats = product_stats.reset_index()

    c1, c2 = st.columns(2)
    
    with c1:
        st.markdown("#### 1️⃣ 제품 포트폴리오 (BCG Matrix)")
        st.caption("가로축: 성장성, 세로축: 매출액")
        
        fig_bcg = px.scatter(
            product_stats,
            x='Growth_Rate',
            y='Total_Sales',
            size='Total_Sales',
            color='제품명',
            hover_name='제품명',
            text='제품명',
            title="성장성 vs 수익성 매트릭스",
            labels={'Growth_Rate': '매출 성장률 (%)', 'Total_Sales': '총 매출액'}
        )
        avg_growth = product_stats['Growth_Rate'].mean()
        avg_sales = product_stats['Total_Sales'].mean()
        
        fig_bcg.add_hline(y=avg_sales, line_dash="dash", line_color="gray", annotation_text="Avg Sales")
        fig_bcg.add_vline(x=avg_growth, line_dash="dash", line_color="gray", annotation_text="Avg Growth")
        fig_bcg.update_traces(textposition='top center')
        st.plotly_chart(fig_bcg, use_container_width=True)
        
    with c2:
        st.markdown("#### 5️⃣ 시장 침투율 (White Space)")
        st.caption("전체 거래처 중 해당 제품을 구매한 병원의 비율입니다.")
        
        total_accounts = df['사업자번호'].nunique()
        product_stats['Penetration'] = (product_stats['Account_Count'] / total_accounts) * 100
        product_stats['Opportunity_Count'] = total_accounts - product_stats['Account_Count']
        product_stats = product_stats.sort_values('Penetration', ascending=True)
        
        fig_white = px.bar(
            product_stats,
            x='Penetration',
            y='제품명',
            orientation='h',
            text='Penetration',
            title=f"제품별 침투율 (전체 거래처 {total_accounts}곳 기준)",
            hover_data=['Opportunity_Count'],
            labels={'Penetration': '침투율(%)', 'Opportunity_Count': '미구매 거래처 수'}
        )
        fig_white.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
        fig_white.update_layout(xaxis_range=[0, 110])
        st.plotly_chart(fig_white, use_container_width=True)

    st.markdown("#### 3️⃣ 제품별 판매 시즌 (Seasonality Heatmap)")
    st.caption("제품별로 매출이 집중되는 시기를 색상으로 보여줍니다. (진할수록 성수기)")
    
    season_pivot = df.pivot_table(index='제품명', columns='월', values='매출액', aggfunc='sum', fill_value=0)
    season_norm = season_pivot.div(season_pivot.max(axis=1), axis=0)
    
    fig_heat = px.imshow(
        season_norm,
        labels=dict(x="월", y="제품", color="매출 집중도"),
        x=season_norm.columns,
        y=season_norm.index,
        color_continuous_scale="Reds",
        aspect="auto"
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    with st.expander("🧩 **함께 팔기(Cross-selling) 기회 분석기** (클릭해서 열기)", expanded=True):
        st.info("💡 **사용법:** '이미 산 제품(A)'과 '아직 안 산 제품(B)'을 선택하면, 영업 타겟 리스트를 뽑아줍니다.")
        
        col_sel1, col_sel2 = st.columns(2)
        all_products = sorted(df['제품명'].unique())
        
        with col_sel1:
            base_prod = st.selectbox("이미 사용 중인 제품 (Anchor)", all_products, index=0)
        with col_sel2:
            target_prod = st.selectbox("추가로 팔고 싶은 제품 (Target)", all_products, index=min(1, len(all_products)-1))
            
        if base_prod == target_prod:
            st.warning("서로 다른 제품을 선택해주세요.")
        else:
            accounts_A = set(df[df['제품명'] == base_prod]['거래처명'].unique())
            accounts_B = set(df[df['제품명'] == target_prod]['거래처명'].unique())
            
            target_accounts = list(accounts_A - accounts_B)
            
            c_res1, c_res2 = st.columns([1, 2])
            
            with c_res1:
                st.metric(f"🎯 영업 기회 병원", f"{len(target_accounts)} 곳", f"잠재 매출 기회")
                st.write(f"**'{base_prod}'** 고객 중 **{(len(target_accounts)/len(accounts_A)*100):.1f}%**가 아직 **'{target_prod}'**를 안 씁니다.")
                
            with c_res2:
                if len(target_accounts) > 0:
                    st.write(f"📋 **타겟 리스트 (상위 10곳 미리보기)**")
                    target_info = df[
                        (df['거래처명'].isin(target_accounts)) & 
                        (df['제품명'] == base_prod)
                    ].groupby(['거래처명', '지역'])['매출액'].sum().reset_index().sort_values('매출액', ascending=False)
                    
                    st.dataframe(
                        target_info.head(50), 
                        column_config={"매출액": st.column_config.NumberColumn(f"{base_prod} 구매력", format="%d M")},
                        hide_index=True,
                        use_container_width=True
                    )
                else:
                    st.success("완벽합니다! 모든 고객이 이미 두 제품을 다 쓰고 있습니다.")

# --------------------------------------------------------------------------------
# 3. 필터 및 URL 제어
# --------------------------------------------------------------------------------
try:
    DRIVE_FILE_ID = st.secrets["DRIVE_FILE_ID"]
except:
    DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"

df_raw = load_data_from_drive(DRIVE_FILE_ID)
if df_raw.empty: st.stop()

def get_p(key, default):
    res = params.get_all(key)
    if not res: return default
    if key in ['y', 'q', 'm']: return [int(x) for x in res]
    return res

sel_years = get_p('y', [df_raw['년'].max()])
sel_channels = get_p('c', sorted(df_raw['판매채널'].unique()))
sel_quarters = get_p('q', sorted(df_raw['분기'].unique()))
sel_months = get_p('m', sorted(df_raw['월'].unique()))

if is_edit_mode:
    with st.sidebar:
        st.header("⚙️ 관리자 필터 설정")
        # [복구] 기존 필터들 모두 복구 완료
        sel_channels = st.multiselect("판매채널", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        sel_quarters = st.multiselect("분기", sorted(df_raw['분기'].unique()), default=sel_quarters)
        
        # 분기 선택에 따른 월 필터 자동 구성
        q_to_m = {1:[1,2,3], 2:[4,5,6], 3:[7,8,9], 4:[10,11,12]}
        avail_m = []
        for q in sel_quarters: avail_m.extend(q_to_m[q])
        sel_months = st.multiselect("월", sorted(avail_m), default=[m for m in sel_months if m in avail_m])
        
        # 제품군 및 제품명 필터 복구
        sel_cats = st.multiselect("제품군", sorted(df_raw['제품군'].unique()), default=sorted(df_raw['제품군'].unique()))
        sel_products = st.multiselect("제품명", sorted(df_raw['제품명'].unique()), default=sorted(df_raw['제품명'].unique()))
        
        st.markdown("---")
        if st.button("🔗 뷰어용 공유 링크 생성"):
            base_url = "https://skbs-sales-2026-cbktkdtxsyrfzfrihefs2h.streamlit.app/" 
            c_encoded = [urllib.parse.quote(val) for val in sel_channels]
            p_string = f"?y={'&y='.join(map(str, sel_years))}&c={'&c='.join(c_encoded)}&q={'&q='.join(map(str, sel_quarters))}&m={'&m='.join(map(str, sel_months))}"
            st.success("아래 링크를 복사하세요!")
            st.code(base_url + p_string, language="text")
else:
    # 뷰어 모드일 때도 기본값 설정
    sel_cats = sorted(df_raw['제품군'].unique())
    sel_products = sorted(df_raw['제품명'].unique())

# 필터링 적용 (사이드바 선택에 따라 움직임)
df_year_filtered = df_raw[df_raw['년'].isin(sel_years)]
df_final = df_year_filtered[
    (df_year_filtered['판매채널'].isin(sel_channels)) &
    (df_year_filtered['분기'].isin(sel_quarters)) &
    (df_year_filtered['월'].isin(sel_months)) &
    (df_year_filtered['제품군'].isin(sel_cats)) &
    (df_year_filtered['제품명'].isin(sel_products))
]

# --------------------------------------------------------------------------------
# 4. 메인 탭 구성
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 1. Overview", "🏆 2. VIP & 이탈 관리", "🔄 3. 재유입 패턴 분석", "🗺️ 4. 지역 분석", "📦 5. 제품 분석"])

# --- [TAB 1] Overview ---
with tab1:
    # 🌟 [추가] 신규 오버뷰 기능 (사이드바 연동)
    render_smart_overview(df_final, df_raw)
    
    st.markdown("---")
    st.markdown("### 📈 기존 성과 요약")
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
            
            styled_vip = (
                top100[['상태', '거래처명', '진료과', '매출액', '수량', '객단가']]
                .style.format({'매출액': '{:,.1f}백만원', '객단가': '{:,.0f}원'})
                .applymap(lambda v: 'color: red; font-weight: bold;' if '이탈' in str(v) else ('color: green; font-weight: bold;' if '정상' in str(v) else ''), subset=['상태'])
            )
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
    # 🌟 [추가] 신규 Win-back 분석 기능 (사이드바 연동)
    # df_raw 전체를 넘기되, 기준 연도는 선택된 연도(sel_years[0])를 사용
    render_winback_quality(df_raw, sel_years[0])
    
    st.markdown("---")
    st.markdown("### 🔄 기존 재유입 분석 (참고용)")
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
    # 🌟 [추가] 신규 지역 심층 분석 (사이드바 연동)
    render_regional_deep_dive(df_final)
    
    st.markdown("---")
    st.markdown("### 🗺️ 지역별 실적 및 심층 내역 (기존)")
    reg_s = df_final.groupby('지역').agg({'매출액': 'sum', '사업자번호': 'nunique'}).reset_index().rename(columns={'사업자번호': '구매처수'}).sort_values('매출액', ascending=False)
    reg_s['마커크기'] = reg_s['매출액'].clip(lower=0)
    c_r1, c_r2 = st.columns([1, 1.5])
    with c_r1:
        st.dataframe(reg_s[['지역', '매출액', '구매처수']].style.format({'매출액': '{:,.0f}백만원'}), use_container_width=True)
        sel_reg = st.selectbox("🔎 분석할 지역 선택", reg_s['지역'].unique() if not reg_s.empty else [None], key="p4_reg")
    with c_r2:
        if not reg_s.empty:
            st.plotly_chart(px.scatter(reg_s, x='구매처수', y='매출액', text='지역', size='마커크기', color='매출액'), use_container_width=True)
    if sel_reg:
        st.markdown("---")
        reg_df = df_final[df_final['지역'] == sel_reg]
        col_reg_a, col_reg_b = st.columns(2)
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
    # 🌟 [추가] 신규 제품 전략 분석 (사이드바 연동)
    render_product_strategy(df_final)
    
    st.markdown("---")
    st.markdown("### 📦 제품별 판매 현황 및 고객 상세 분석 (기존)")
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
