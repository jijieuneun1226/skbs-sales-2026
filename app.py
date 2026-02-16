import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import urllib.parse
import numpy as np
import io
import requests

# 1. 페이지 설정
st.set_page_config(page_title="SKBS Sales Report", layout="wide")
st.title("📊 SKBS Sales Report")

# 2. 데이터 로드 함수 (안정성 강화 버전)
@st.cache_data(ttl=3600)
def load_data_from_drive(file_id):
    # 일반 엑셀 파일용 다운로드 URL
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        # 바이트 스트림으로 읽어 ZIP 에러 방지
        file_bytes = io.BytesIO(response.content)
        df = pd.read_excel(file_bytes, engine='openpyxl')
        return df
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}")
        return pd.DataFrame()

# 3. 데이터 로드 실행
DRIVE_FILE_ID = "1lFGcQST27rBuUaXcuOJ7yRnMlQWGyxfr"
df_raw = load_data_from_drive(DRIVE_FILE_ID)

if not df_raw.empty:
    # 컬럼명 전처리 (이미지에 나타난 '제 품 명' 등 공백 제거)
    df_raw.columns = df_raw.columns.astype(str).str.replace(' ', '').str.strip()

    try:
        # 필수 열 매칭 및 변환
        if '매출일자' in df_raw.columns:
            df_raw['매출일자'] = pd.to_datetime(df_raw['매출일자'], errors='coerce')
            df_raw = df_raw.dropna(subset=['매출일자'])
            df_raw['년월'] = df_raw['매출일자'].dt.strftime('%Y-%m')
            df_raw['년'] = df_raw['매출일자'].dt.year

        # 숫자 변환
        for col in ['합계금액', '수량']:
            if col in df_raw.columns:
                df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce').fillna(0)
        
        df_raw['매출액(M)'] = df_raw.get('합계금액', 0) / 1000000

        st.success("✅ 데이터 로드 성공!")

        # ----------------------------------------------------------------------
        # 4. 분석 대시보드 출력 (Tab 구성)
        # ----------------------------------------------------------------------
        tab1, tab2, tab3, tab4 = st.tabs(["📊 요약", "🏥 거래처 분석", "📦 제품 분석", "🔍 원본 데이터"])

        with tab1:
            c1, c2, c3 = st.columns(3)
            c1.metric("총 매출액", f"{df_raw['매출액(M)'].sum():,.1f} M")
            c2.metric("총 거래처", f"{df_raw['거래처명'].nunique():,} 곳")
            c3.metric("총 판매수량", f"{df_raw['수량'].sum():,.0f} 개")

            # 월별 추이
            monthly = df_raw.groupby('년월')['매출액(M)'].sum().reset_index()
            fig_line = px.line(monthly, x='년월', y='매출액(M)', title="월별 매출 흐름", markers=True)
            st.plotly_chart(fig_line, use_container_width=True)

        with tab2:
            st.subheader("🏥 상위 거래처 실적")
            top_h = df_raw.groupby('거래처명')['매출액(M)'].sum().sort_values(ascending=False).head(20).reset_index()
            st.plotly_chart(px.bar(top_h, x='매출액(M)', y='거래처명', orientation='h', color='매출액(M)'), use_container_width=True)

        with tab3:
            st.subheader("📦 제품별 매출 비중")
            # 이미지에서 확인된 '제품명' 컬럼 사용
            p_col = '제품명' if '제품명' in df_raw.columns else df_raw.columns[0]
            prod_s = df_raw.groupby(p_col)['매출액(M)'].sum().reset_index()
            st.plotly_chart(px.pie(prod_s, values='매출액(M)', names=p_col, hole=0.4), use_container_width=True)

        with tab4:
            st.dataframe(df_raw)

    except Exception as e:
        st.error(f"⚠️ 데이터 처리 중 오류: {e}")
        st.write("현재 컬럼명 목록:", list(df_raw.columns))
else:
    st.warning("데이터를 불러올 수 없습니다. 파일 ID나 공유 권한을 확인해 주세요.")
