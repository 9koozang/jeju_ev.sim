import streamlit as st
import pandas as pd
import numpy as np
import random
import math
import plotly.graph_objects as go

# ==========================================
# 0. 페이지 설정 및 데이터 로드
# ==========================================
st.set_page_config(page_title="제주 EV 핫스팟 스케줄링", layout="wide")
st.title("⚡ 제주도 EV '핫스팟 집중관리' 분산 시뮬레이터")
st.markdown("전체 1,616개 충전소 중 **주요 밀집 구역(상위 20%)**에 수요를 집중시키고, 오버부킹 시 **전체 인프라로 분산**시키는 현실적인 모델입니다.")

EXCEL_PATH = '제주도_충전소_아파트제외_최종.xlsx'
TOTAL_SLOTS = 24 * 6 # 10분 단위 (하루 144슬롯)

@st.cache_data
def load_full_data():
    try:
        df = pd.read_excel(EXCEL_PATH)
        df = df.dropna(subset=['lat', 'lng', 'statNm', 'addr'])
        def assign_region(addr):
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
# 1. 사이드바 (현실적 변수 설정)
# ==========================================
st.sidebar.header("📋 현실 기반 시뮬레이션 설정")
daily_requests = st.sidebar.slider("일일 총 예약 요청 (제주도 실제 수준)", 5000, 15000, 10000, step=1000)
search_radius = st.sidebar.slider("대안 탐색 반경 (km)", 1, 20, 10)
reward_val = st.sidebar.number_input("기본 이동 보상 (원)", value=3000)
time_cost = st.sidebar.number_input("시간 비용 (원/분)", value=200)

# ==========================================
# 2. 시뮬레이션 엔진 (핵심 의사결정 로직 포함)
# ==========================================
def run_hotspot_sim():
    stations = df_stations.to_dict('records')
    total_count = len(stations)
    
    hotspot_count = int(total_count * 0.2)
    hotspot_indices = set(random.sample(range(total_count), hotspot_count))
    
    charger_slots = {i: np.zeros(TOTAL_SLOTS) for i in range(total_count)}
    occupancy_counts = np.zeros(total_count)
    
    conflicts = 0
    moved = 0
    redirect_paths = []

    for _ in range(daily_requests):
        req_slot = int(np.random.normal(90, 15)) 
        req_slot = max(0, min(TOTAL_SLOTS - 6, req_slot))
        
        charge_mins = max(10, int(np.random.lognormal(3.2, 0.5)))
        duration = math.ceil(charge_mins / 10)
        
        if np.random.rand() < 0.8:
            target_idx = random.choice(list(hotspot_indices))
        else:
            target_idx = random.randint(0, total_count - 1)
            
        source_st = stations[target_idx]
        
        # 빈 자리일 경우 정상 예약
        if np.sum(charger_slots[target_idx][req_slot:req_slot+duration]) == 0:
            if np.random.rand() > 0.15: # 노쇼 15% 반영
                charger_slots[target_idx][req_slot:req_slot+duration] = 1
                occupancy_counts[target_idx] += charge_mins
        
        # ★ 오버부킹 발생 (충돌) ★
        else:
            conflicts += 1
            
            # 1. 같은 권역 내 대안 충전소 먼저 검색 (배터리 가능 여부를 판단하기 위해)
            same_region_stations = [
                (i, s) for i, s in enumerate(stations) 
                if s['region'] == source_st['region'] and i != target_idx
            ]
            
            best_alt = None
            min_dist = 999
            for i, alt_st in same_region_stations:
                dist = math.sqrt((source_st['lat']-alt_st['lat'])**2 + (source_st['lng']-alt_st['lng'])**2) * 111
                if dist < search_radius and dist < min_dist:
                    if np.sum(charger_slots[i][req_slot:req_slot+duration]) == 0:
                        min_dist = dist
                        best_alt = (i, alt_st, dist)
            
            # 대안 충전소가 존재할 경우에만 제안 로직 가동
            if best_alt:
                # 배터리(SOC) 랜덤 부여 (5% ~ 50%)
                soc_org = random.randint(5, 50) # 선예약자
                soc_ovb = random.randint(5, 50) # 오버부킹 예약자
                
                # ★ 다음 예약자 침범 여부 체크 ★
                # 현재 차가 대기했다가 충전할 시간(wait_start ~ wait_end)에 예약이 있는지 확인
                wait_start = req_slot + duration
                wait_end = min(TOTAL_SLOTS, wait_start + duration)
                
                intrusion = False
                if wait_start < TOTAL_SLOTS:
                    if np.sum(charger_slots[target_idx][wait_start:wait_end]) > 0:
                        intrusion = True # 다음 예약자 있음! 침범!
                else:
                    intrusion = True # 자정을 넘어감 (침범)
                
                # [서브 함수] 현재 배터리로 이동 가능한지 체크 (1%당 3km 이동 가능 가정)
                def can_reach(soc, dist):
                    return (soc * 3.0) >= dist
                
                # [서브 함수] 사용자의 선택(효용) 계산
                def calculate_choice(soc, dist, is_forced):
                    if not can_reach(soc, dist): 
                        return False # 가고 싶어도 배터리 없어서 못 감
                    
                    # 강제 이동(침범 시)이면 보상을 2배로 빵빵하게 줌!
                    actual_reward = reward_val * 2 if is_forced else reward_val
                    move_utility = actual_reward - (dist/30*60 * time_cost) - (dist * 50)
                    wait_loss = -(charge_mins * time_cost)
                    
                    return move_utility > wait_loss # 이득이면 True(이동 수락)

                resolved = False
                
                # ★ 민경님의 의사결정 트리 ★
                if soc_org > soc_ovb:
                    # Case 1. 선예약자가 배터리 더 많음 -> 선예약자에게 먼저 제안
                    # (선예약자는 자기 권리가 있으니 강제 이동 대상이 아님)
                    if calculate_choice(soc_org, min_dist, is_forced=False):
                        resolved = True
                    else:
                        # 선예약자가 거절함 -> 오버부킹 예약자에게 제안
                        # 오버부킹 예약자는 침범 시 강제 이동(is_forced=intrusion) 대상
                        resolved = calculate_choice(soc_ovb, min_dist, is_forced=intrusion)
                else:
                    # Case 2. 선예약자가 배터리가 더 적음 -> 선예약자 충전 시작
                    # 오버부킹 예약자에게 바로 제안
                    resolved = calculate_choice(soc_ovb, min_dist, is_forced=intrusion)
                
                # 누군가 1명이라도 이동을 수락했다면 해결(Success)로 간주!
                if resolved:
                    moved += 1
                    # 둘 중 누가 이동했든, 결국 '대안 충전소'의 슬롯이 하나 차는 것은 수학적으로 동일함
                    charger_slots[best_alt[0]][req_slot:req_slot+duration] = 1
                    occupancy_counts[best_alt[0]] += charge_mins
                    
                    if len(redirect_paths) < 1000:
                        redirect_paths.append((source_st, best_alt[1]))

    return conflicts, moved, occupancy_counts, redirect_paths, hotspot_indices

if st.sidebar.button("🚀 시뮬레이션 실행", type="primary"):
    with st.spinner("핫스팟 의사결정 로직 연산 중..."):
        c, m, occ, paths, hotspots = run_hotspot_sim()
    
    hotspot_occ = [occ[i] for i in hotspots]
    non_hotspot_occ = [occ[i] for i in range(len(occ)) if i not in hotspots]
    
    avg_hotspot_util = np.mean(hotspot_occ) / (24 * 60) * 100
    avg_total_util = np.mean(occ) / (24 * 60) * 100

    # ==========================================
    # 3. 성과 지표 
    # ==========================================
    st.markdown("### 📊 스케줄링 성과 지표 (동적 배터리/침범 제어 로직 적용)")
    col1, col2, col3, col4 = st.columns(4)
    
    col1.metric("총 예약 요청", f"{daily_requests:,} 건")
    col2.metric("오버부킹(충돌) 발생", f"{c:,} 건")
    
    # 실패 = 이동 안함(대기함). 따라서 분산 성공(이동)만 해결률로 계산
    resolution_rate = (m / c * 100) if c > 0 else 0
    col3.metric("오버부킹 해결률 (이동 성공)", f"{resolution_rate:.1f} %", f"외곽 충전소로 {m:,}건 분산")
    
    col4.metric("주요 거점(핫스팟) 가동률", f"{avg_hotspot_util:.1f} %", f"전체 평균 가동률은 {avg_total_util:.1f}%", delta_color="off")

    st.divider()

    # ==========================================
    # 4. 지도 시각화
    # ==========================================
    st.subheader("🗺️ 수요 집중 구역(핫스팟) ➔ 외곽 충전소 유도 현황 (초록선=이동 성공)")
    
    fig = go.Figure()

    fig.add_trace(go.Scattermapbox(
        lat=df_stations['lat'], lon=df_stations['lng'],
        mode='markers',
        marker=go.scattermapbox.Marker(
            size=[10 if i in hotspots else 4 for i in range(len(df_stations))],
            color=occ, 
            colorscale='Hot', 
            showscale=True,
            colorbar=dict(title="가동시간(분)")
        ),
        text=[f"[{'핫스팟' if i in hotspots else '일반'}] {name}" for i, name in enumerate(df_stations['statNm'])],
        name='충전소'
    ))

    for p in paths:
        fig.add_trace(go.Scattermapbox(
            lat=[p[0]['lat'], p[1]['lat']],
            lon=[p[0]['lng'], p[1]['lng']],
            mode='lines',
            line=dict(width=1.5, color='rgba(0, 255, 100, 0.6)'), # 초록색 선
            hoverinfo='none',
            showlegend=False
        ))

    fig.update_layout(
        mapbox=dict(
            style="carto-darkmatter",
            center=dict(lat=33.38, lon=126.55),
            zoom=9.5
        ),
        margin={"r":0,"t":0,"l":0,"b":0},
        height=700
    )
    
    st.plotly_chart(fig, use_container_width=True)

else:
    st.info("👈 일일 요청 수를 설정하고 시뮬레이션을 실행해보세요! 배터리 기반 의사결정 로직이 작동합니다.")