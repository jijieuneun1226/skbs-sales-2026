import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import io
import requests

# --------------------------------------------------------------------------------
# ★ 구글 드라이브 설정 (본인의 파일 ID를 입력하세요)
# --------------------------------------------------------------------------------
FILE_ID = "여기에_복사한_구글드라이브_파일ID를_넣으세요"
url = f'https://drive.google.com/uc?id=104499051275921137043&rtpof=true&sd=true'

# --------------------------------------------------------------------------------
# 2. 데이터 로드 (구글 드라이브 직결 방식)
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600) # 1시간 동안 캐시 유지
def load_drive_data():
    try:
        response = requests.get(url)
        # 메모리에 파일을 올려서 읽어옵니다 (대용량 대응)
        f = io.BytesIO(response.content)
        df = pd.read_excel(f, engine='openpyxl')
        
        df.columns = df.columns.astype(str).str.strip()
        
        # [전처리 로직 통합]
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
                if cand.replace(' ', '') in current_cols:
                    df.rename(columns={current_cols[cand.replace(' ', '')]: std_col}, inplace=True)
                    break

        df['매출일자'] = pd.to_datetime(df['매출일자'])
        df['년'] = df['매출일자'].dt.year
        df['분기'] = df['매출일자'].dt.quarter
        df['월'] = df['매출일자'].dt.month
        df['년월'] = df['매출일자'].dt.strftime('%Y-%m')
        
        for col in ['합계금액', '수량']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
                
        df['매출액'] = df['합계금액'] / 1000000
        for col in ['거래처그룹', '제품명', '제품군', '진료과', '지역']:
            if col in df.columns:
                df[col] = df[col].astype(str).replace('nan', '미분류')
        return df.sort_values(by=['사업자번호', '매출일자'])
    except Exception as e:
        st.error(f"구글 드라이브 데이터를 불러오는 중 오류 발생: {e}")
        st.stop()

df_raw = load_drive_data()

# --------------------------------------------------------------------------------
# 3. 사이드바 및 분석 탭 (이전 필터 및 드릴다운 로직 그대로 사용)
# --------------------------------------------------------------------------------
# [사용자님의 이전 최종 필터 및 탭 로직을 여기에 붙여넣으세요]