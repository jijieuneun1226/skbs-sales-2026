import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import io
import requests

# 1. 페이지 설정
st.set_page_config(page_title="SKBS Sales Report", layout="wide")
st.title("📊 SKBS Sales Report (2026)")

# 2. 데이터 로드 함수 (보안 및 접근성 강화)
@st.cache_data(ttl=60)
def load_data_from_drive(file_id):
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    try:
        response = requests.get(url)
        response.raise_for_status()
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
    # [핵심] 열 이름에서 공백을 제거하여 인식률을 높입니다.
    df_raw.columns = df_raw.columns.astype(str).str.replace(' ', '').str.strip()

    # 필수 열이 삭제되었을 경우를 대비한 자동 매칭 및 복구 로직
    col_mapping = {
        '매출일자': ['매출일자', '날짜', '일자'],
        '제품명': ['제품명', '품목명', '제 품 명'],
        '합계금액': ['합계금액', '매출액', '금액'],
        '수량': ['수량', '판매수량'],
        '거래처명': ['거래처명', '병원명']
    }

    for std_name, candidates in col_mapping.items():
        if std_name not in df_raw.columns:
            for cand in candidates:
                if cand in df_raw.columns:
                    df_raw.rename(columns={cand: std_name}, inplace=True)
                    break
            # 끝내 못 찾으면 빈 값으로 생성 (에러 방지)
            if std_name not in df_raw.columns:
                df_raw[std_name] = pd.NA

    try:
        # 데이터 타입 변환
        if '매출일자' in df_raw.columns:
            df_raw['매출일자'] = pd.to_datetime(df_raw['매출일자'], errors='coerce')
            df_raw = df_raw.dropna(subset=['매출일자'])
            df_raw['년'] = df_raw['매출일자'].dt.year
            df_raw['년월'] = df_raw['매출일자'].dt.strftime('%Y-%m')
        
        for col in ['합계금액', '수량']:
            df_raw[col] = pd.to_numeric(df_raw.get(col, 0), errors='coerce').fillna(0)
        
        df_raw['매출액(M)'] = df_raw['합계금액'] / 1000000

        # 4. 필터링 (2026년 데이터 우선)
        available_years = sorted(df_raw['년'].unique(), reverse=True)
        default_yr = [2026] if 2026 in available_years else [available_years[0]]
        sel_years = st.sidebar.multiselect("조회 년도", available_years, default=default_yr)
        
        df_final = df_raw[df_raw['년'].isin(sel_years)]

        # 5. 화면 출력
        st.success(f"✅ 데이터 로드 완료! (현재 열 목록: {', '.join(df_raw.columns)})")
        
        tab1, tab2, tab3 = st.tabs(["📊 요약", "🏥 거래처", "📦 제품"])

        with tab1:
            c1, c2, c3 = st.columns(3)
            c1.metric("총 매출", f"{df_final['매출액(M)'].sum():,.1f} M")
            c2.metric("거래처 수", f"{df_final['거래처명'].nunique()} 곳")
            c3.metric("판매량", f"{df_final['수량'].sum():,.0f} 개")
            
            monthly = df_final.groupby('년월')['매출액(M)'].sum().reset_index()
            st.plotly_chart(px.line(monthly, x='년월', y='매출액(M)', markers=True), use_container_width=True)

        with tab2:
            top_h = df_final.groupby('거래처명')['매출액(M)'].sum().sort_values(ascending=False).head(15).reset_index()
            st.plotly_chart(px.bar(top_h, x='매출액(M)', y='거래처명', orientation='h'), use_container_width=True)

        with tab3:
            prod_s = df_final.groupby('제품명')['매출액(M)'].sum().reset_index()
            st.plotly_chart(px.pie(prod_s, values='매출액(M)', names='제품명'), use_container_width=True)

    except Exception as e:
        st.error(f"처리 오류: {e}")
        st.write("엑셀에 필수 열(매출일자, 합계금액 등)이 있는지 확인해 주세요.")

else:
    st.warning("데이터를 불러올 수 없습니다. 파일 ID나 공유 설정을 확인해 주세요.")
