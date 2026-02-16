import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import urllib.parse
import numpy as np
import io
import requests

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
    .info-box {padding: 15px; border-radius: 5px; font-size: 14px; margin-bottom: 20px; border: 1px solid #e0e0e0; background-color: #ffffff;}
</style>
""", unsafe_allow_html=True)

st.title("📊 SKBS Sales Report")

# --------------------------------------------------------------------------------
# 2. 데이터 로드 및 전처리 (ID 고정 및 안정화 버전)
# --------------------------------------------------------------------------------
@st.cache_data(ttl=60) # 데이터 수정이 잦으므로 캐시를 짧게 유지합니다.
def load_data_from_drive(file_id):
    # 구글 드라이브 직링크 (ZIP 에러 방지용)
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        file_bytes = io.BytesIO(response.content)
        df = pd.read_excel(file_bytes, engine='openpyxl')
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}")
        return pd.DataFrame()

    # 컬럼명 전처리: 모든 공백 제거 (제 품 명 -> 제품명 대응)
    df.columns = df.columns.astype(str).str.replace(' ', '').str.strip()
    
    col_map = {
        '매출일자': ['매출일자', '날짜', 'Date', '일자'],
        '제품명': ['제품명변환', '제품명', '품목명'],
        '합계금액': ['합계금액', '매출액', '금액'],
        '수량': ['수량', '판매수량'],
        '사업자번호': ['사업자번호', '사업자등록번호', '거래처코드'],
        '거래처명': ['거래처명', '병원명'],
        '주소': ['주소', 'Address', '사업장주소']
    }
    
    for std_col, candidates in col_map.items():
        if std_col in df.columns: continue
        for cand in candidates:
            if cand in df.columns:
                df.rename(columns={cand: std_col}, inplace=True)
                break

    try:
        # 날짜 처리
        df['매출일자'] = pd.to_datetime(df['매출일자'], errors='coerce')
        df = df.dropna(subset=['매출일자'])
        df['년'] = df['매출일자'].dt.year
        df['분기'] = df['매출일자'].dt.quarter
        df['월'] = df['매출일자'].dt.month
        df['년월'] = df['매출일자'].dt.strftime('%Y-%m')
        
        # 숫자 데이터 처리
        for col in ['합계금액', '수량']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0
        df['매출액'] = df['합계금액'] / 1000000
        
        # 지역 표준화
        if '지역' not in df.columns and '주소' in df.columns:
            df['지역'] = df['주소'].astype(str).str[:2] # 앞 두 글자 추출
        elif '지역' not in df.columns:
            df['지역'] = '미분류'
            
    except Exception as e:
        st.error(f"전처리 오류: {e}")
        return pd.DataFrame()
    return df

# --------------------------------------------------------------------------------
# 3. 데이터 실행 및 필터링 (2026년 대응)
# --------------------------------------------------------------------------------
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
df_raw = load_data_from_drive(DRIVE_FILE_ID)

if df_raw.empty:
    st.stop()

# 년도 필터 (2026년이 있으면 기본값으로 설정)
available_years = sorted(df_raw['년'].unique(), reverse=True)
default_yr = [2026] if 2026 in available_years else [available_years[0]]

if is_edit_mode:
    with st.sidebar:
        st.header("⚙️ 관리자 필터")
        sel_years = st.multiselect("년도", available_years, default=default_yr)
else:
    sel_years = default_yr

df_final = df_raw[df_raw['년'].isin(sel_years)]

# --------------------------------------------------------------------------------
# 4. 분석 리포트 화면 구성 (탭)
# --------------------------------------------------------------------------------
tab1, tab2, tab3 = st.tabs(["📊 성과 요약", "🏥 거래처 분석", "📦 제품 분석"])

with tab1:
    st.subheader(f"🚀 {sel_years}년 성과 요약")
    c1, c2, c3 = st.columns(3)
    c1.metric("총 매출액", f"{df_final['매출액'].sum():,.1f} M")
    c2.metric("활성 거래처 수", f"{df_final['거래처명'].nunique():,} 곳")
    c3.metric("총 판매량", f"{df_final['수량'].sum():,.0f} 개")

    st.markdown("---")
    st.markdown("#### 월별 매출 추이")
    monthly = df_final.groupby('년월')['매출액'].sum().reset_index()
    st.plotly_chart(px.line(monthly, x='년월', y='매출액', markers=True), use_container_width=True)

with tab2:
    st.subheader("🏆 상위 매출 거래처 Top 20")
    top_cust = df_final.groupby('거래처명')['매출액'].sum().sort_values(ascending=False).head(20).reset_index()
    st.plotly_chart(px.bar(top_cust, x='매출액', y='거래처명', orientation='h', color='매출액'), use_container_width=True)

with tab3:
    st.subheader("📦 제품별 매출 점유율")
    prod_sales = df_final.groupby('제품명')['매출액'].sum().reset_index()
    st.plotly_chart(px.pie(prod_sales, values='매출액', names='제품명', hole=0.4), use_container_width=True)

# 데이터 확인용
with st.expander("🔍 업로드된 데이터 미리보기 (최근 100건)"):
    st.dataframe(df_final.sort_values('매출일자', ascending=False).head(100))
