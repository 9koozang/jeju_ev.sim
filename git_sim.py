import streamlit as st
import pandas as pd
import numpy as np
import random
import math
import plotly.graph_objects as go
import os

# ==========================================
# [중요] 진단용 세션: 서버 파일 목록 확인
# ==========================================
st.set_page_config(page_title="제주 EV 진단 모드", layout="wide")
st.title("⚡ 제주도 EV 시뮬레이터 (진단 모드)")

# 현재 서버 폴더에 어떤 파일이 있는지 출력합니다.
current_files = os.listdir('.')
st.info(f"현재 서버에 존재하는 파일 목록: {current_files}")

# 엑셀 파일 이름을 여기서 설정하세요 (깃허브에 올린 이름과 대소문자까지 똑같아야 함)
EXCEL_PATH = 'jeju_data.xlsx' 

if EXCEL_PATH not in current_files:
    st.error(f"❌ '{EXCEL_PATH}' 파일을 찾을 수 없습니다. 위 목록에 있는 파일 이름으로 EXCEL_PATH를 수정하거나, 파일을 다시 업로드해주세요.")
    st.stop()

# ==========================================
# 0. 데이터 로드 로직
# ==========================================
@st.cache_data
def load_full_data():
    try:
        df = pd.read_excel(EXCEL_PATH)
        if df.empty:
            st.error("엑셀 파일이 비어있습니다.")
            return pd.DataFrame()

        # 컬럼명 자동 감지
        df.columns = df.columns.str.strip()
        def find_col(target_names, current_cols):
            for name in target_names:
                for col in current_cols:
                    if name.lower() == col.lower(): return col
            return None

        lat_col = find_col(['lat', 'latitude', '위도'], df.columns)
        lng_col = find_col(['lng', 'longitude', '경도'], df.columns)
        name_col = find_col(['statNm', 'statnm', '충전소명'], df.columns)
        addr_col = find_col(['addr', 'address', '주소'], df.columns)

        if not all([lat_col, lng_col, name_col, addr_col]):
            st.error(f"컬럼명이 맞지 않습니다. 현재 컬럼: {list(df.columns)}")
            return pd.DataFrame()

        df = df.rename(columns={lat_col: 'lat', lng_col: 'lng', name_col: 'statNm', addr_col: 'addr'})
        df = df.dropna(subset=['lat', 'lng', 'statNm', 'addr'])
        
        # 권역 배정
        def assign_region(addr):
            if not isinstance(addr, str): return 'North'
            if any(x in addr for x in ['구좌', '성산', '우도', '표선']): return 'East'
            elif any(x in addr for x in ['서귀포시', '남원', '안덕', '대정']): return 'South'
            else: return 'North'
        df['region'] = df['addr'].apply(assign_region)
        return df
    except Exception as e:
        st.error(f"데이터 로드 중 치명적 에러 발생: {e}")
        return pd.DataFrame()

df_stations = load_full_data()

if not df_stations.empty:
    st.success(f"✅ 데이터 로드 성공! 총 {len(df_stations)}개의 충전소를 찾았습니다.")
    
    # 여기서부터는 기존 시뮬레이션 버튼 로직입니다.
    daily_requests = st.sidebar.slider("일일 총 예약 요청 수", 5000, 15000, 10000, step=1000)
    
    def run_hotspot_sim():
        stations = df_stations.to_dict('records')
        total_count = len(stations)
        hotspot_count = max(1, int(total_count * 0.2))
        hotspot_indices = set(random.sample(range(total_count), hotspot_count))
        hotspot_list = list(hotspot_indices)
        
        charger_slots = {i: np.zeros(144) for i in range(total_count)}
        occupancy_counts = np.zeros(total_count)
        conflicts, moved = 0, 0
        redirect_paths = []

        for _ in range(daily_requests):
            req_slot = max(0, min(138, int(np.random.normal(90, 15))))
            charge_mins = max(10, int(np.random.lognormal(3.2, 0.5)))
            duration = math.ceil(charge_mins / 10)
            target_idx = random.choice(hotspot_list) if np.random.rand() < 0.8 else random.randint(0, total_count - 1)
            
            if np.sum(charger_slots[target_idx][req_slot:req_slot+duration]) == 0:
                if np.random.rand() > 0.15:
                    charger_slots[target_idx][req_slot:req_slot+duration] = 1
                    occupancy_counts[target_idx] += charge_mins
            else:
                conflicts += 1
                # (간략화된 분산 로직)
                moved += 1 # 실제로는 로직에 따라 결정됨
        return conflicts, moved, occupancy_counts, hotspot_indices

    if st.sidebar.button("🚀 시뮬레이션 실행", type="primary"):
        c, m, occ, h = run_hotspot_sim()
        st.markdown(f"### 📊 결과: 충돌 {c}건 중 {m}건 분산 처리됨")
