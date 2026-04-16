import streamlit as st
import pandas as pd
import numpy as np
import random
import math
import plotly.graph_objects as go

# [버전 확인용] v1.2 - 컬럼명 자동 감지 및 에러 방지 로직 강화
# ==========================================
# 0. 페이지 설정 및 데이터 로드
# ==========================================
st.set_page_config(page_title="제주 EV 핫스팟 스케줄링", layout="wide")
st.title("⚡ 제주도 EV '핫스팟 집중관리' 분산 시뮬레이터")

EXCEL_PATH = 'jeju_data.xlsx'
TOTAL_SLOTS = 24 * 6 

@st.cache_data
def load_full_data():
    try:
        df = pd.read_excel(EXCEL_PATH)
        if df.empty:
            st.error("엑셀 파일이 비어있습니다.")
            return pd.DataFrame()

        # 1. 컬럼명 전처리 (공백 제거 및 소문자화하여 매칭 확률 높임)
        df.columns = df.columns.str.strip()
        orig_cols = list(df.columns)
        
        # 2. 필수 컬럼 찾기 (대소문자 달라도 찾을 수 있게 수정)
        def find_col(target_names, current_cols):
            for name in target_names:
                for col in current_cols:
                    if name.lower() == col.lower(): return col
            return None

        lat_col = find_col(['lat', 'latitude', '위도'], orig_cols)
        lng_col = find_col(['lng', 'longitude', '경도'], orig_cols)
        name_col = find_col(['statNm', 'statnm', '충전소명', '이름'], orig_cols)
        addr_col = find_col(['addr', 'address', '주소'], orig_cols)

        if not all([lat_col, lng_col, name_col, addr_col]):
            st.error(f"엑셀 컬럼명이 일치하지 않습니다. 현재 컬럼: {orig_cols}")
            st.info("필요한 컬럼명: lat, lng, statNm, addr")
            return pd.DataFrame()

        # 3. 코드에서 쓰기 편하게 컬럼명 통일
        df = df.rename(columns={lat_col: 'lat', lng_col: 'lng', name_col: 'statNm', addr_col: 'addr'})
        
        # 4. 결측치 제거
        df = df.dropna(subset=['lat', 'lng', 'statNm', 'addr'])
        
        if df.empty:
            st.error("필수 항목(위도, 경도 등)에 유효한 데이터가 없습니다.")
            return pd.DataFrame()

        def assign_region(addr):
            if not isinstance(addr, str): return 'North'
            if '구좌' in addr or '성산' in addr or '우도' in addr or '표선' in addr: return 'East'
            elif '서귀포시' in addr or '남원' in addr or '안덕' in addr or '대정' in addr: return 'South'
            else: return 'North' 
        df['region'] = df['addr'].apply(assign_region)
        return df
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}")
        return pd.DataFrame()

df_stations = load_full_data()

# ==========================================
# 1. 사이드바 설정
# ==========================================
st.sidebar.header("📋 현실 기반 시뮬레이션 설정")
daily_requests = st.sidebar.slider("일일 총 예약 요청 수", 5000, 15000, 10000, step=1000)
search_radius = st.sidebar.slider("대안 탐색 반경 (km)", 1, 20, 10)
reward_val = st.sidebar.number_input("기본 이동 보상 (원)", value=3000)
time_cost = st.sidebar.number_input("시간 비용 (원/분)", value=200)

# ==========================================
# 2. 시뮬레이션 엔진
# ==========================================
def run_hotspot_sim():
    if df_stations.empty:
        return 0, 0, np.zeros(1), [], set()

    stations = df_stations.to_dict('records')
    total_count = len(stations)
    
    # IndexError 방지: 최소 1개 보장
    hotspot_count = max(1, int(total_count * 0.2))
    hotspot_indices = set(random.sample(range(total_count), hotspot_count))
    hotspot_list = list(hotspot_indices) # 리스트화
    
    charger_slots = {i: np.zeros(TOTAL_SLOTS) for i in range(total_count)}
    occupancy_counts = np.zeros(total_count)
    
    conflicts, moved = 0, 0
    redirect_paths = []

    for _ in range(daily_requests):
        req_slot = max(0, min(TOTAL_SLOTS - 6, int(np.random.normal(90, 15))))
        charge_mins = max(10, int(np.random.lognormal(3.2, 0.5)))
        duration = math.ceil(charge_mins / 10)
        
        # 핫스팟 타겟팅
        if np.random.rand() < 0.8:
            target_idx = random.choice(hotspot_list)
        else:
            target_idx = random.randint(0, total_count - 1)
            
        source_st = stations[target_idx]
        
        if np.sum(charger_slots[target_idx][req_slot:req_slot+duration]) == 0:
            if np.random.rand() > 0.15: 
                charger_slots[target_idx][req_slot:req_slot+duration] = 1
                occupancy_counts[target_idx] += charge_mins
        else:
            conflicts += 1
            same_region_stations = [
                (i, s) for i, s in enumerate(stations) 
                if s.get('region') == source_st.get('region') and i != target_idx
            ]
            
            best_alt = None
            min_dist = 999
            for i, alt_st in same_region_stations:
                dist = math.sqrt((source_st['lat']-alt_st['lat'])**2 + (source_st['lng']-alt_st['lng'])**2) * 111
                if dist < search_radius and dist < min_dist:
                    if np.sum(charger_slots[i][req_slot:req_slot+duration]) == 0:
                        min_dist, best_alt = dist, (i, alt_st, dist)
            
            if best_alt:
                soc_org, soc_ovb = random.randint(5, 50), random.randint(5, 50)
                wait_start = req_slot + duration
                wait_end = min(TOTAL_SLOTS, wait_start + duration)
                intrusion = (np.sum(charger_slots[target_idx][wait_start:wait_end]) > 0) if wait_start < TOTAL_SLOTS else True
                
                def calculate_choice(soc, dist, is_forced):
                    if (soc * 3.0) < dist: return False 
                    actual_reward = reward_val * 2 if is_forced else reward_val
                    move_utility = actual_reward - (dist/30*60 * time_cost) - (dist * 50)
                    wait_loss = -(charge_mins * time_cost)
                    return move_utility > wait_loss

                resolved = False
                if soc_org > soc_ovb:
                    if calculate_choice(soc_org, min_dist, False): resolved = True
                    else: resolved = calculate_choice(soc_ovb, min_dist, intrusion)
                else:
                    resolved = calculate_choice(soc_ovb, min_dist, intrusion)
                
                if resolved:
                    moved += 1
                    charger_slots[best_alt[0]][req_slot:req_slot+duration] = 1
                    occupancy_counts[best_alt[0]] += charge_mins
                    if len(redirect_paths) < 1000:
                        redirect_paths.append((source_st, best_alt[1]))

    return conflicts, moved, occupancy_counts, redirect_paths, hotspot_indices

# ==========================================
# 3. 결과 출력부
# ==========================================
if st.sidebar.button("🚀 시뮬레이션 실행", type="primary"):
    if df_stations.empty:
        st.warning("데이터가 정상적으로 로드되지 않았습니다. 상단의 에러 메시지를 확인해주세요.")
    else:
        with st.spinner("데이터 분석 중..."):
            c, m, occ, paths, hotspots = run_hotspot_sim()
        
        if hotspots:
            hotspot_occ = [occ[i] for i in hotspots]
            avg_hotspot_util = np.mean(hotspot_occ) / (24 * 60) * 100
            avg_total_util = np.mean(occ) / (24 * 60) * 100

            st.markdown("### 📊 스케줄링 성과 지표")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("총 예약 요청", f"{daily_requests:,} 건")
            col2.metric("오버부킹 발생", f"{c:,} 건")
            res_rate = (m / c * 100) if c > 0 else 0
            col3.metric("오버부킹 해결률", f"{res_rate:.1f} %", f"{m:,}건 분산 성공")
            col4.metric("핫스팟 가동률", f"{avg_hotspot_util:.1f} %", f"전체 평균 {avg_total_util:.1f}%")

            st.divider()
            st.subheader("🗺️ 수요 분산 시각화 (이동 성공 경로 표시)")
            fig = go.Figure()
            fig.add_trace(go.Scattermapbox(
                lat=df_stations['lat'], lon=df_stations['lng'], mode='markers',
                marker=go.scattermapbox.Marker(
                    size=[10 if i in hotspots else 4 for i in range(len(df_stations))],
                    color=occ, colorscale='Hot', showscale=True,
                    colorbar=dict(title="분")
                ),
                text=df_stations['statNm']
            ))
            for p in paths:
                fig.add_trace(go.Scattermapbox(
                    lat=[p[0]['lat'], p[1]['lat']], lon=[p[0]['lng'], p[1]['lng']],
                    mode='lines', line=dict(width=1.5, color='rgba(0, 255, 100, 0.6)'),
                    showlegend=False
                ))
            fig.update_layout(
                mapbox=dict(style="carto-darkmatter", center=dict(lat=33.38, lon=126.55), zoom=9.5),
                margin={"r":0,"t":0,"l":0,"b":0}, height=700
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("데이터가 부족합니다.")
else:
    st.info("👈 일일 요청 수를 설정하고 시뮬레이션을 실행해보세요!")
