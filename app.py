import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import urllib.parse
import numpy as np
import requests
import io

# --------------------------------------------------------------------------------
# 1. 페이지 설정
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
# 2. 데이터 로드 (핵심: 강제 다운로드 + 헤더 위치 자동 찾기)
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def load_data_from_drive(file_id):
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    
    try:
        # [1단계] 파일 강제 다운로드 (requests 사용)
        session = requests.Session()
        response = session.get(url, stream=True)
        
        # 구글 드라이브 대용량 파일 경고(쿠키) 처리
        token = None
        for key, value in response.cookies.items():
            if key.startswith('download_warning'):
                token = value
                break
        if token:
            url = f"https://drive.google.com/uc?export=download&confirm={token}&id={file_id}"
            response = session.get(url, stream=True)
            
        if response.status_code != 200:
            st.error(f"❌ 다운로드 실패 (Status Code: {response.status_code})")
            return pd.DataFrame()

        # [2단계] '매출일자'가 있는 진짜 헤더 행(Row) 찾기
        file_bytes = io.BytesIO(response.content)
        
        # 일단 처음 20줄만 읽어서 탐색
        try:
            df_preview = pd.read_excel(file_bytes, header=None, nrows=20, engine='openpyxl')
        except:
            # 엑셀이 아니면 CSV로 재시도
            file_bytes.seek(0)
            df_preview = pd.read_csv(file_bytes, header=None, nrows=20, encoding='cp949')

        target_keyword = "매출일자"
        header_row_index = 0
        found_header = False
        
        for idx, row in df_preview.iterrows():
            # 행 전체를 문자열로 변환 후 공백 제거해서 검색
            row_str = row.astype(str).str.replace(" ", "").values
            if any(target_keyword in str(x) for x in row_str):
                header_row_index = idx
                found_header = True
                break
        
        if not found_header:
            st.warning("⚠️ '매출일자' 컬럼을 자동으로 찾지 못했습니다. 첫 번째 줄을 제목으로 가정합니다.")

        # [3단계] 진짜 헤더 위치부터 다시 읽기
        file_bytes.seek(0)
        try:
            df = pd.read_excel(file_bytes, header=header_row_index, engine='openpyxl')
        except:
            file_bytes.seek(0)
            df = pd.read_csv(file_bytes, header=header_row_index, encoding='cp949')

    except Exception as e:
        st.error(f"❌ 파일 읽기 오류: {e}")
        return pd.DataFrame()

    # ------------------------------------------------------
    # 전처리 (컬럼 매핑 및 정리)
    # ------------------------------------------------------
    df.columns = df.columns.astype(str).str.strip()
    
    col_map = {
        '매출일자': ['매출일자', '날짜', 'Date', '일자'],
        '제품명': ['제품명 변환', '제 품 명', '제품명', '품목명'],
        '합계금액': ['합계금액', '공급가액', '금액', '매출액'],
        '수량': ['수 량', '수량', 'Qty'],
        '사업자번호': ['사업자번호', '사업자등록번호', 'Biz No'],
        '거래처명': ['거래처명', '병원명', '요양기관명'],
        '진료과': ['진료과', '진료과목'],
        '제품군': ['제품군', '카테고리'],
        '거래처그룹': ['거래처그룹', '그룹', '판매채널'],
        '주소': ['도로명주소', '주소', '사업장주소', '지번주소'],
        '지역': ['지역', '시도']
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
        # 지역 자동 생성 (주소 기반)
        if '지역' not in df.columns and '주소' in df.columns:
            df['지역_임시'] = df['주소'].astype(str).str.split().str[0]
            addr_map = {
                '서울': '서울', '서울시': '서울', '서울특별시': '서울',
                '경기': '경기', '경기도': '경기',
                '부산': '부산', '부산시': '부산', '부산광역시': '부산',
                '대구': '대구', '대구시': '대구', '대구광역시': '대구',
                '인천': '인천', '인천시': '인천', '인천광역시': '인천',
                '광주': '광주', '광주시': '광주', '광주광역시': '광주',
                '대전': '대전', '대전시': '대전', '대전광역시': '대전',
                '울산': '울산', '울산시': '울산', '울산광역시': '울산',
                '세종': '세종', '세종시': '세종', '세종특별자치시': '세종',
                '강원': '강원', '강원도': '강원', '강원특별자치도': '강원',
                '충북': '충북', '충청북도': '충북',
                '충남': '충남', '충청남도': '충남',
                '전북': '전북', '전라북도': '전북', '전북특별자치도': '전북',
                '전남': '전남', '전라남도': '전남',
                '경북': '경북', '경상북도': '경북',
                '경남': '경남', '경상남도': '경남',
                '제주': '제주', '제주도': '제주', '제주특별자치도': '제주'
            }
            df['지역'] = df['지역_임시'].map(addr_map).fillna('기타')
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
        else:
            # 비상용 날짜 생성
            df['매출일자'] = pd.to_datetime('2024-01-01')
            df['년'] = 2024; df['분기'] = 1; df['월'] = 1; df['년월'] = '2024-01'

        # 기타 컬럼 정리
        if '제품명' in df.columns:
            df['제품명'] = df['제품명'].astype(str).str.replace(r'\(.*?\)', '', regex=True).str.strip()
        else: df['제품명'] = '미분류'
            
        for col in ['합계금액', '수량']:
            if col not in df.columns: df[col] = 0
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        df['매출액'] = df['합계금액'] / 1000000
        
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
        st.error(f"❌ 데이터 전처리 오류: {e}")
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
        past_years = [y for y in cust_year.columns if y < target_year - 1]
        has_history = cust_year.loc[biz_no, past_years].sum() > 0 if past_years else False
        
        if has_ty:
            if has_t1: status = "✅ 기존 (유지)"
            else: status = "🔄 재유입 (복귀)" if has_history else "🆕 신규 (New)"
        else:
            status = "📉 이탈"
        classification[biz_no] = status
    base_info['상태'] = base_info.index.map(classification)
    return base_info

# --------------------------------------------------------------------------------
# 📊 [Executive] 임원 보고용 스마트 오버뷰
# --------------------------------------------------------------------------------
def render_smart_overview(df_curr, df_raw):
    if df_curr.empty: return
    current_year = int(df_curr['년'].max())
    last_year = current_year - 1
    selected_months = df_curr['월'].unique()
    df_prev = df_raw[(df_raw['년'] == last_year) & (df_raw['월'].isin(selected_months))]

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
        with c1:
            st.metric(label="💰 총 매출 실적", value=f"{sales_curr:,.0f} M", delta=f"{sales_pct:+.1f}% (전년 동기 대비)")
            st.area_chart(df_curr.groupby('월')['매출액'].sum(), height=50, color="#FF4B4B")
        with c2:
            st.metric(label="🏥 총 거래 병원", value=f"{total_cust} 곳")
            st.markdown(f"- **✨ 신규:** <span style='color:blue'>+{new_cust}</span> / **💔 이탈:** <span style='color:red'>-{lost_cust}</span>", unsafe_allow_html=True)
            if total_cust > 0: st.progress((retained_cust / total_cust), text=f"고객 유지율 {(retained_cust/total_cust)*100:.1f}%")
        with c3:
            top_prod = df_curr.groupby('제품명')['매출액'].sum().idxmax()
            top_prod_sales = df_curr.groupby('제품명')['매출액'].sum().max()
            st.metric(label="🏆 Best Product", value=top_prod)
            st.write(f"매출 기여: **{top_prod_sales:,.0f}M**")

# --------------------------------------------------------------------------------
# 🕵️‍♂️ [Deep Dive] 심층 분석 인사이트
# --------------------------------------------------------------------------------
def render_advanced_insights(df, tab_name):
    if df.empty: return
    total_sales = df['매출액'].sum()
    total_qty = df['수량'].sum()
    total_count = df['사업자번호'].nunique()
    avg_price = (total_sales / total_qty * 1000000) if total_qty > 0 else 0
    avg_rev = (total_sales / total_count) if total_count > 0 else 0

    with st.container(border=True):
        st.markdown(f"### 📊 {tab_name} 심층 분석")
        
        if tab_name == "매출 추세":
            monthly = df.groupby('월')[['매출액', '수량']].sum()
            mid = len(monthly) // 2
            trend_msg = "데이터 부족"
            if mid > 0:
                diff_sales = ((monthly.iloc[mid:]['매출액'].mean() - monthly.iloc[:mid]['매출액'].mean()) / monthly.iloc[:mid]['매출액'].mean().replace(0,1)) * 100
                diff_qty = ((monthly.iloc[mid:]['수량'].mean() - monthly.iloc[:mid]['수량'].mean()) / monthly.iloc[:mid]['수량'].mean().replace(0,1)) * 100
                if diff_sales > 0 and diff_qty < 0: trend_msg = "💰 **단가 주도 성장:** 수량 감소, 단가 상승"
                elif diff_sales > 0 and diff_qty > 0: trend_msg = "🚀 **양적 성장:** 수량/매출 동반 상승"
                elif diff_sales < 0: trend_msg = "📉 **전반적 부진:** 매출 감소세"
            
            c1, c2, c3 = st.columns(3)
            c1.metric("💰 총 매출", f"{total_sales:,.0f}M")
            c2.metric("📦 총 판매량", f"{total_qty:,.0f}개")
            c3.metric("🏷️ 평균 단가", f"{avg_price:,.0f}원")
            st.info(trend_msg)

        elif tab_name == "제품별 분석":
            prod_stats = df.groupby('제품명').agg({'매출액': ['sum', 'count']})
            prod_stats.columns = ['Sales', 'Count']
            top1 = prod_stats.sort_values('Sales', ascending=False).index[0]
            share = (prod_stats.loc[top1, 'Sales'] / total_sales) * 100
            c1, c2 = st.columns(2)
            c1.metric("🏆 1위 제품", top1, f"점유율 {share:.1f}%")
            c2.metric("📊 판매 품목 수", f"{len(prod_stats)}개")
            st.info(f"**Insight:** 1위 제품('{top1}') 의존도가 **{share:.1f}%** 입니다.")

        elif tab_name == "거래처 분석":
            acc_sales = df.groupby('거래처명')['매출액'].sum().sort_values(ascending=False)
            top10_share = (acc_sales.head(10).sum() / total_sales) * 100
            c1, c2 = st.columns(2)
            c1.metric("🏥 활성 거래처", f"{total_count}곳")
            c2.metric("💎 상위 10곳 점유율", f"{top10_share:.1f}%")
            st.warning(f"**Insight:** 거래처 한 곳당 평균 **{avg_rev:,.0f}백만원**의 매출을 올립니다.")

        elif tab_name == "지역별 분석":
            reg_stats = df.groupby('지역')['매출액'].sum()
            top_reg = reg_stats.idxmax()
            c1, c2 = st.columns(2)
            c1.metric("📍 1위 지역", top_reg, f"{reg_stats.max():,.0f}M")
            c2.metric("🗺️ 지역 수", f"{len(reg_stats)}개")
            st.info(f"**Insight:** **{top_reg}** 지역이 전체 매출의 {(reg_stats.max()/total_sales*100):.1f}%를 차지합니다.")

# --------------------------------------------------------------------------------
# ♻️ [Win-back] 재유입 분석
# --------------------------------------------------------------------------------
def render_winback_quality(df, current_year):
    last_year = current_year - 1
    curr = df[df['년'] == current_year].groupby(['거래처명', '지역'])['매출액'].sum()
    prev = df[df['년'] == last_year].groupby(['거래처명', '지역'])['매출액'].sum()
    hist = df[df['년'] < current_year].groupby(['거래처명', '지역'])['매출액'].max()
    
    winback = (curr.index.difference(prev.index)).intersection(hist.index)
    if len(winback) == 0:
        st.info("♻️ 재유입된 거래처가 없습니다.")
        return

    df_wb = pd.DataFrame({'올해': curr[winback], '과거최고': hist[winback]})
    df_wb['회복률'] = (df_wb['올해'] / df_wb['과거최고'] * 100).fillna(0)
    df_wb['상태'] = df_wb['회복률'].apply(lambda x: "🟢 완전" if x>=80 else ("🔴 간보기" if x<20 else "🟡 회복중"))
    df_wb = df_wb.reset_index().rename(columns={'index':'거래처명'}) 

    st.markdown(f"### ♻️ {current_year}년 재유입 분석")
    c1, c2 = st.columns(2)
    c1.metric("돌아온 거래처", f"{len(df_wb)}곳")
    c2.metric("확보 매출", f"{df_wb['올해'].sum():,.0f}M")
    
    fig = px.scatter(df_wb, x='과거최고', y='올해', color='상태', hover_data=['거래처명'], title="과거 vs 현재 매출")
    fig.add_shape(type="line", x0=0, y0=0, x1=df_wb['과거최고'].max(), y1=df_wb['과거최고'].max(), line=dict(color="gray", dash="dash"))
    st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------------
# 🗺️ & 💊 추가 심층 분석
# --------------------------------------------------------------------------------
def render_regional_deep_dive(df):
    if df.empty: return
    reg_stats = df.groupby('지역').agg(Sales=('매출액','sum'), Count=('사업자번호','nunique')).reset_index()
    reg_stats['Eff'] = reg_stats['Sales'] / reg_stats['Count']
    st.markdown("#### 1️⃣ 지역 효율성 (거래처수 vs 객단가)")
    fig = px.scatter(reg_stats, x='Count', y='Eff', size='Sales', color='지역', text='지역')
    st.plotly_chart(fig, use_container_width=True)

def render_product_strategy(df):
    if df.empty: return
    stats = df.groupby('제품명').agg(Sales=('매출액','sum')).reset_index()
    st.markdown("#### 1️⃣ 제품 포트폴리오 (매출 규모)")
    fig = px.bar(stats.sort_values('Sales'), x='Sales', y='제품명', orientation='h')
    st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------------
# 3. 메인 실행 및 필터
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
        st.header("⚙️ 관리자 필터")
        sel_channels = st.multiselect("채널", sorted(df_raw['판매채널'].unique()), default=sel_channels)
        sel_years = st.multiselect("년도", sorted(df_raw['년'].unique(), reverse=True), default=sel_years)
        sel_quarters = st.multiselect("분기", sorted(df_raw['분기'].unique()), default=sel_quarters)
        st.markdown("---")
        if st.button("🔗 링크 생성"):
            base = "https://skbs-sales-2026-cbktkdtxsyrfzfrihefs2h.streamlit.app/"
            c_enc = [urllib.parse.quote(v) for v in sel_channels]
            url = f"{base}?y={'&y='.join(map(str,sel_years))}&c={'&c='.join(c_enc)}"
            st.code(url)

sel_cats = sorted(df_raw['제품군'].unique())
sel_products = sorted(df_raw['제품명'].unique())

df_year = df_raw[df_raw['년'].isin(sel_years)]
df_final = df_year[
    (df_year['판매채널'].isin(sel_channels)) &
    (df_year['분기'].isin(sel_quarters)) &
    (df_year['월'].isin(sel_months)) &
    (df_year['제품군'].isin(sel_cats)) &
    (df_year['제품명'].isin(sel_products))
]

# --------------------------------------------------------------------------------
# 4. 탭 구성
# --------------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "🏆 VIP 관리", "🔄 재유입", "🗺️ 지역", "📦 제품"])

with tab1:
    render_smart_overview(df_final, df_raw)
    render_advanced_insights(df_final, "매출 추세")
    st.markdown("---")
    st.subheader("📅 월별 추이")
    monthly = df_final.groupby('년월').agg({'매출액':'sum', '사업자번호':'nunique'}).reset_index()
    fig = go.Figure()
    fig.add_trace(go.Bar(x=monthly['년월'], y=monthly['매출액'], name='매출'))
    fig.add_trace(go.Scatter(x=monthly['년월'], y=monthly['사업자번호'], name='거래처수', yaxis='y2'))
    fig.update_layout(yaxis2=dict(overlaying='y', side='right'))
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    render_advanced_insights(df_final, "거래처 분석")
    st.markdown("### 🏆 VIP 리스트")
    if not df_final.empty:
        vip = (
            df_final.groupby(['거래처명','진료과'])
            .agg({'매출액':'sum'})
            .reset_index()
            .sort_values('매출액', ascending=False)
            .head(50)
        )
        st.dataframe(vip.style.format({'매출액':'{:,.1f}M'}), use_container_width=True)
        
    st.markdown("---")
    st.markdown("### 📊 거래처 상태 분류")
    cls = classify_customers(df_raw, sel_years[0])
    c1, c2 = st.columns([1,2])
    with c1: st.dataframe(cls['상태'].value_counts())
    with c2: st.plotly_chart(px.pie(cls, names='상태'), use_container_width=True)

with tab3:
    render_winback_quality(df_raw, sel_years[0])

with tab4:
    render_advanced_insights(df_final, "지역별 분석")
    render_regional_deep_dive(df_final)
    st.markdown("---")
    reg_s = df_final.groupby('지역')['매출액'].sum().reset_index().sort_values('매출액', ascending=False)
    st.plotly_chart(px.bar(reg_s, x='지역', y='매출액'), use_container_width=True)

with tab5:
    render_advanced_insights(df_final, "제품별 분석")
    render_product_strategy(df_final)
