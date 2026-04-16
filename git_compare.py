import streamlit as st
import pandas as pd
import numpy as np
import random
import math
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ==========================================
# 0. 기본 설정 및 데이터 로드
# ==========================================
st.set_page_config(page_title="제주 EV 스케줄링 최종 비교", layout="wide")
st.title("🏆 AS-IS vs TO-BE 통합 성과 분석 리포트")
st.markdown("충전소 가동률, 처리량, 사업자 순이익 및 고객 만족도를 정량적으로 비교 분석합니다.")

EXCEL_PATH = 'jeju.data.xlsx'
TOTAL_SLOTS = 24 * 6 
PROFIT_PER_MIN = 300 # 1분당 충전 수익 마진 가정

@st.cache_data
def load_full_data():
    df = pd.read_excel(EXCEL_PATH)
    df = df.dropna(subset=['lat', 'lng', 'statNm', 'addr'])
    def assign_region(addr):
        if '구좌' in addr or '성산' in addr or '우도' in addr or '표선' in addr: return 'East'
        elif '서귀포시' in addr or '남원' in addr or '안덕' in addr or '대정' in addr: return 'South'
        else: return 'North' 
    df['region'] = df['addr'].apply(assign_region)
    return df

df_stations = load_full_data()

# ==========================================
# 1. 사이드바 설정
# ==========================================
st.sidebar.header("📋 실험 환경 및 정책 설정")
daily_requests = st.sidebar.slider("일일 예약 요청 수", 5000, 15000, 10000, step=1000)
search_radius = st.sidebar.slider("TO-BE 대안 탐색 반경 (km)", 1, 20, 10)
reward_val = st.sidebar.number_input("TO-BE 이동 보상금 (원)", value=3000)
time_cost_val = st.sidebar.number_input("시간 가치 비용 (원/분)", value=200)

# ==========================================
# 2. 공통 수요 생성기 (취소/노쇼 랜덤성 반영)
# ==========================================
def generate_demand(num_requests, num_stations, hotspots):
    np.random.seed(42) # 비교의 공정성을 위해 고정
    requests = []
    hotspots_list = list(hotspots)
    
    # 오늘의 환경 변수 (랜덤 확률 결정)
    rate_free = random.uniform(0.08, 0.15)
    rate_pen = random.uniform(0.03, 0.08)
    rate_no = random.uniform(0.03, 0.08)
    
    for _ in range(num_requests):
        target = random.choice(hotspots_list) if np.random.rand() < 0.8 else random.randint(0, num_stations - 1)
        req_slot = max(0, min(TOTAL_SLOTS - 6, int(np.random.normal(90, 15))))
        charge_mins = max(10, int(np.random.lognormal(3.2, 0.5)))
        
        rand_val = np.random.rand()
        if rand_val < rate_free: status = 'Cancel_Free'
        elif rand_val < rate_free + rate_pen: status = 'Cancel_Penalty'
        elif rand_val < rate_free + rate_pen + rate_no: status = 'No_Show'
        else: status = 'Show'
            
        requests.append({
            'target': target, 'slot': req_slot, 'dur': math.ceil(charge_mins / 10),
            'mins': charge_mins, 'status': status,
            'soc_org': random.randint(5, 55), 'soc_ovb': random.randint(5, 55)
        })
    return requests, rate_free, rate_pen, rate_no

# ==========================================
# 3. 시뮬레이션 엔진
# ==========================================
def run_simulation(requests, stations, hotspots, is_tobe=False):
    charger_slots = {i: np.zeros(TOTAL_SLOTS) for i in range(len(stations))}
    occ_minutes = np.zeros(len(stations))
    
    metrics = {
        "throughput": 0, "failures": 0, "redirected": 0,
        "rev_charge": 0, "rev_penalty": 0, "cost_reward": 0
    }
    
    for req in requests:
        target, slot, dur = req['target'], req['slot'], req['dur']
        expected_rev = req['mins'] * PROFIT_PER_MIN
        
        # 1. 취소/노쇼 로직
        if req['status'] == 'Cancel_Free': continue
        if req['status'] == 'Cancel_Penalty':
            metrics["rev_penalty"] += expected_rev * 0.3
            continue
        if req['status'] == 'No_Show':
            metrics["rev_penalty"] += expected_rev * 0.5
            if not is_tobe: charger_slots[target][slot:slot+dur] = 1 # AS-IS는 노쇼가 자리 차지
            continue
            
        # 2. 정상 이용 시도
        if np.sum(charger_slots[target][slot:slot+dur]) == 0:
            charger_slots[target][slot:slot+dur] = 1
            occ_minutes[target] += req['mins']
            metrics["throughput"] += 1
            metrics["rev_charge"] += expected_rev
        else:
            # 3. 오버부킹 발생
            if not is_tobe:
                metrics["failures"] += 1
            else:
                source_st = stations[target]
                same_reg = [(i, s) for i, s in enumerate(stations) if s['region'] == source_st['region'] and i != target]
                
                best_alt = None
                min_d = 999
                for i, alt_st in same_reg:
                    d = math.sqrt((source_st['lat']-alt_st['lat'])**2 + (source_st['lng']-alt_st['lng'])**2) * 111
                    if d < search_radius and d < min_d:
                        if np.sum(charger_slots[i][slot:slot+dur]) == 0:
                            min_d, best_alt = d, i
                
                resolved = False
                if best_alt is not None:
                    intrusion = (slot+dur < TOTAL_SLOTS and np.sum(charger_slots[target][slot+dur:min(TOTAL_SLOTS, slot+dur*2)]) > 0)
                    reward = reward_val * 2 if intrusion else reward_val
                    
                    # 효용 계산 (can_reach 로직 포함)
                    move_u = reward - (min_d/30*60 * time_cost_val) - (min_d * 50)
                    can_go = max(req['soc_org'], req['soc_ovb']) * 3.0 >= min_d
                    
                    if can_go and move_u > -(req['mins'] * time_cost_val):
                        resolved = True
                        metrics["redirected"] += 1
                        charger_slots[best_alt][slot:slot+dur] = 1
                        occ_minutes[best_alt] += req['mins']
                        metrics["throughput"] += 1
                        metrics["rev_charge"] += expected_rev
                        metrics["cost_reward"] += reward
                
                if not resolved: metrics["failures"] += 1

    # 지표 산출
    hot_occ = [occ_minutes[i] for i in hotspots]
    out_occ = [occ_minutes[i] for i in range(len(occ_minutes)) if i not in hotspots]
    
    metrics["util_hotspot"] = np.mean(hot_occ) / (24 * 60) * 100
    metrics["util_outskirt"] = np.mean(out_occ) / (24 * 60) * 100
    metrics["util_total"] = np.mean(occ_minutes) / (24 * 60) * 100
    metrics["total_profit"] = metrics["rev_charge"] + metrics["rev_penalty"] - metrics["cost_reward"]
    
    total_shows = sum(1 for r in requests if r['status'] == 'Show')
    metrics["service_level"] = (metrics["throughput"] / total_shows * 100) if total_shows > 0 else 0
    return metrics

# ==========================================
# 4. 메인 실행부
# ==========================================
if st.sidebar.button("🚀 전체 시뮬레이션 및 비교 시작", type="primary"):
    with st.spinner("1,616개 충전소 데이터 기반 정밀 분석 중..."):
        stations_list = df_stations.to_dict('records')
        hotspots = set(random.sample(range(len(stations_list)), int(len(stations_list) * 0.2)))
        
        demand, r_free, r_pen, r_no = generate_demand(daily_requests, len(stations_list), hotspots)
        
        asis = run_simulation(demand, stations_list, hotspots, is_tobe=False)
        tobe = run_simulation(demand, stations_list, hotspots, is_tobe=True)

    # 지표 출력 (이전 사진 스타일 + 신규 지표)
    st.markdown("### 📊 운영 가동률 및 처리량 비교")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 충전 완료 대수", f"{tobe['throughput']:,} 대", f"+{tobe['throughput']-asis['throughput']:,} 대 개선")
    col2.metric("핫스팟 가동률", f"{tobe['util_hotspot']:.1f} %", f"{tobe['util_hotspot']-asis['util_hotspot']:.1f} %p")
    col3.metric("외곽 충전소 가동률", f"{tobe['util_outskirt']:.1f} %", f"{tobe['util_outskirt']-asis['util_outskirt']:.1f} %p")
    col4.metric("전체 평균 가동률", f"{tobe['util_total']:.1f} %", f"{tobe['util_total']-asis['util_total']:.1f} %p")

    st.markdown("### 💰 재무적 수익 및 서비스 레벨 비교")
    colA, colB, colC, colD = st.columns(4)
    colA.metric("사업자 순이익", f"{tobe['total_profit']:,.0f} 원", f"{tobe['total_profit']-asis['total_profit']:,.0f} 원")
    colB.metric("고객 서비스 레벨", f"{tobe['service_level']:.1f} %", f"{tobe['service_level']-asis['service_level']:.1f} %p")
    colC.metric("서비스 실패(낙오)", f"{tobe['failures']:,} 명", f"{tobe['failures']-asis['failures']:,} 명", delta_color="inverse")
    colD.metric("오버부킹 분산 성공", f"{tobe['redirected']:,} 건", "TO-BE 전용")

    st.caption(f"※ 환경 설정 - 무료취소: {r_free*100:.1f}% | 수수료취소: {r_pen*100:.1f}% | 노쇼: {r_no*100:.1f}%")
    st.divider()

    # 시각화 그래프
    st.subheader("📉 시각적 성과 비교")
    fig = make_subplots(rows=1, cols=2, subplot_titles=("주요 지표별 성과 비교", "구역별 가동률 밸런싱"))
    
    # 1번 차트: 처리량 및 순이익(스케일 조정 위해 처리량 위주)
    fig.add_trace(go.Bar(name="AS-IS", x=["충전 완료(대)", "서비스 실패(명)"], y=[asis['throughput'], asis['failures']], marker_color='indianred'), row=1, col=1)
    fig.add_trace(go.Bar(name="TO-BE", x=["충전 완료(대)", "서비스 실패(명)"], y=[tobe['throughput'], tobe['failures']], marker_color='lightseagreen'), row=1, col=1)
    
    # 2번 차트: 가동률 비교
    fig.add_trace(go.Bar(name="AS-IS", x=["핫스팟", "외곽", "전체"], y=[asis['util_hotspot'], asis['util_outskirt'], asis['util_total']], marker_color='indianred', showlegend=False), row=1, col=2)
    fig.add_trace(go.Bar(name="TO-BE", x=["핫스팟", "외곽", "전체"], y=[tobe['util_hotspot'], tobe['util_outskirt'], tobe['util_total']], marker_color='lightseagreen', showlegend=False), row=1, col=2)
    
    fig.update_layout(height=450, barmode='group')
    st.plotly_chart(fig, use_container_width=True)

    st.success("✅ 시뮬레이션 결과: 오버부킹 제도는 핫스팟의 병목을 해결하고 외곽 자원을 활성화하여 사업자 수익과 고객 만족도를 동시에 증진시킵니다.")
else:
    st.info("👈 좌측에서 변수를 설정하고 버튼을 눌러 A/B 테스트를 진행하세요.")
