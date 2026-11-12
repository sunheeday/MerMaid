# 실주행 데이터셋 조사 (시뮬레이터 보완용)

- 조사일: 2026-11-12. 웹 검색과 각 데이터셋 페이지 확인 결과입니다. 표의 "확인 안 됨"은 공개 페이지에서 확인하지 못했다는 뜻이고, 신청하거나 받아 보면 달라질 수 있습니다.
- 목적: 멀미 라벨이 없어도, 실제 주행의 가속도(가능하면 자이로, 속도, CAN)를 trip.bin으로 바꿔 시뮬레이터 대신 또는 함께 쓰기. 라벨은 지금처럼 Oman 식으로 만듭니다 (SPEC.md 3.7).

## 0. 우리에게 필요한 신호

| 등급 | 신호 | 쓰는 곳 |
|---|---|---|
| 꼭 필요 | 3축 가속도, 연속 기록 수십 분 이상, 25 Hz 이상 (100 Hz면 좋음) | G1 피처 전부, Oman |
| 있으면 좋음 | 자이로 z (yaw), 속도 (바퀴 속도나 GNSS) | yaw_rms, G2, 휴식 판정, **라벨용 운동학 가속도** (a_long = dv/dt, a_lat = v·ω_z) |
| 더 좋음 | CAN 페달, 조향, 기어, 회생, SOC | G3a, G3b |
| 최고 | 승객 MISC 기록 | 실제 정확도 검증 |

샘플링 주기에 대해: Oman 입력(0.63 Hz 저역통과)과 ISO Wf(0.08~0.63 Hz)는 10 Hz로도 계산할 수 있습니다. 하지만 저크 RMS, 노면 진동(2~15 Hz), 급가속 횟수의 순간 판정은 100 Hz 쪽이 정확합니다.

## 1. 결론

- **멀미 라벨과 차량 신호가 함께 공개된 데이터셋은 찾지 못했습니다** (Hugging Face, AI Hub, 공공데이터포털, 4TU, IEEE DataPort 포함).
- **차량 신호만 있는 실주행 데이터는 충분합니다.** 바로 쓸 수 있는 순서:
  1. **comma2k19** (Hugging Face, MIT): 100 Hz 가속도·자이로 + CAN 바퀴 속도·조향, 33시간. 고속도로뿐.
  2. **CANTraj (옛 DriveDNA-GPS)** (Hugging Face, 연구용, 승인 필요): 약 100 Hz IMU와 100 Hz CAN(속도, 가속도, yaw rate, 페달, 기어)이 있는 주행이 약 1,700~1,960개. 시내 포함, 신호 구성이 가장 좋음.
  3. **L2D** (Hugging Face): 독일 30개 도시의 EV(기아 e-Niro) 시내 주행, CAN 속도·페달·조향·기어. 10 Hz로 다시 샘플링돼 있고 라이선스를 확인하지 못함.
- **국내 도로**는 **KAIST Complex Urban Dataset**(서울 강남·여의도, 판교, 가속도·자이로·바퀴 엔코더·VRS GPS 속도)이 가장 현실적입니다.
- **AI Hub**: 연속 IMU/CAN 시계열이 있는 주행 데이터셋을 확인하지 못했습니다. 대부분 영상·라이다 중심이거나 시뮬레이션이고, 내국인만 신청할 수 있는 경우가 많습니다.

## 2. 후보 비교

### 2.1 Hugging Face

| 데이터셋 | 가속도 · 자이로 | 속도 · CAN | 규모 · 지역 | 라이선스 · 접근 | 적합도 |
|---|---|---|---|---|---|
| [commaai/comma2k19](https://huggingface.co/datasets/commaai/comma2k19) | LSM6DS3 100 Hz 가속도·자이로 (앞·오른쪽·아래 좌표), 자력계 10 Hz | CAN 전체 기록. 바퀴 속도, 조향각, 레이더가 해석돼 있음 | 33시간 이상, 1분 구간 2,019개, 캘리포니아 280번 고속도로 (San Jose~SF), Civic · RAV4, 약 100 GB | MIT, 바로 받음 | ★★★ 바로 시작용. 고속도로뿐이라 회전·정차가 적음 |
| [HenryYHW/DriveDNA-GPS](https://huggingface.co/datasets/HenryYHW/DriveDNA-GPS) (CANTraj) | 자이로·가속도 약 100 Hz (전체 로그 1,957개 주행), 나머지는 약 1 Hz | carState 100 Hz (1,686개 주행): 속도, 가속도, 조향각·속도, yaw rate, 바퀴 속도, 가속·브레이크 페달, 깜빡이, 크루즈, 기어 | 3,988개 주행, 운전자 468명, GPS 궤적 1,665시간. SF 베이 지역, 애틀랜타 등. Ford, 현대·기아·제네시스, VW 등 | 연구용 라이선스, 수동 승인, 약 29 GB | ★★★ 신호가 가장 풍부하고 시내 포함. 승인 필요 |
| [yaak-ai/L2D](https://huggingface.co/blog/lerobot-goes-to-driving-school) | IMU 가속도 x, y만 (자이로 언급 없음) | CAN 속도, 가속·브레이크 페달, 조향각, 깜빡이, 기어 | 5,000시간 이상, 독일 30개 도시, 운전학원 EV 60대 (기아 e-Niro). 전체 90 TB 이상이지만 에피소드 단위 스트리밍 가능 | 라이선스 확인 안 됨 | ★★ EV 시내 주행, 페달 있음. **모든 신호 10 Hz**라 저크·진동 피처는 부정확 |
| [InterestingITS/smartphone_in_vehicle_multisensor](https://huggingface.co/datasets/InterestingITS/smartphone_in_vehicle_multisensor) | 스마트폰 가속도 (주기 확인 안 됨) | OBD-II 속도, RTK GNSS 속도·위치 | Golf · Passat, 고속도로·국도·시내 14구간, 좌표상 중국 창춘 부근 | CC BY 4.0 | ★★ 폰을 차에 둔 상황이라 우리 장착과 비슷. 자이로 확인 필요 |
| [shchon11/TCar_hyu](https://huggingface.co/datasets/shchon11/TCar_hyu) | NovAtel INS 100 Hz (INSPVA), corrimu 포함 (주기 확인 안 됨) | 해석된 차량 CAN (`/bsw/vehicle_can`), 원시 CAN, 차량 상태 | 20초 장면 682개 = 3.8시간 (녹화 54개). 기준점이 경기 화성(37.20°N, 126.83°E, K-City 부근). 2 TB 이상 | 승인 필요, 라이선스·기관 표기 없음 | ★ 국내 차량 CAN이지만 20초 조각이라 Oman 누적에 짧음. 이름의 "hyu"가 한양대일 수 있어 출처를 물어볼 만함 |
| [HenryYHW/BATON](https://huggingface.co/datasets/HenryYHW/BATON) | 3축 가속도·자이로 | CAN 신호, 레이더 | 운전 제어권 전환(takeover) 데이터 | CC BY-NC 4.0, 원본은 승인 필요 | ★ 목적이 달라 주행 구간이 짧을 수 있음 |
| [madhurbehl/RACECAR_DATA](https://huggingface.co/datasets/madhurbehl/RACECAR_DATA) | 125 Hz 가속도·자이로 | 레이더 원시 CAN | 경주차 | – | ✕ 승용차 주행과 너무 다름 |

### 2.2 그 밖의 해외 공개 데이터

| 데이터셋 | 신호 | 규모 · 지역 | 라이선스 | 적합도 |
|---|---|---|---|---|
| [nuScenes CAN bus expansion](https://arxiv.org/pdf/1903.11027) | IMU, 자세, 조향, 브레이크, 가속 페달, 바퀴 속도 4개, 기어, 배터리, 토크 (`scene-XXXX_ms_imu.json`, `zoe_veh_info.json`) | 보스턴, 싱가포르. 20초 장면 1,000개 | nuScenes 약관 (비상업) | ★ 장면이 짧음 |
| [A2D2 (Audi)](https://www.a2d2.audi/en/) | 차량 버스: 병진·회전 속도와 가속도, 조향각, 가속 페달, 브레이크 (주기 확인 안 됨) | 독일 3개 도시 | CC BY-ND 4.0 | ★★ 연속 주행 시퀀스 있음. 주기 확인 필요 |
| PVS (Passive Vehicular Sensors) | 대시보드 좌·우 가속도·자이로, 속도 | 브라질, 차 3대, 주행 9개 | 확인 안 됨 | ★★ 비포장·노면 진동 자료로 좋음 |
| [Reconstructed Roundabout Driving](https://ieee-dataport.org/node/4458) | 다듬은 속도·가속도 (회전교차로) | 호주 ACFR 회전교차로 5곳 기반 | IEEE DataPort 무료 계정 | ★ 회전교차로 장면 보정용 |
| [KIT Reference Driving Scenarios](https://b2find.eudat.eu/dataset/54a2e910-4744-520b-8db0-82d40a99e68e) | 멀미 관련 주파수 성분이 있는 기준 경로 | 시험로 + 실도로 | CC BY-SA 4.0 | ★★ 멀미 연구용 기준 주행. 시뮬레이터 경로 검증에 좋음 |

### 2.3 국내

| 데이터셋 | 신호 | 규모 · 지역 | 접근 | 적합도 |
|---|---|---|---|---|
| [KAIST Complex Urban Dataset](https://sites.google.com/view/complex-urban-dataset/format) | IMU Ver2(urban05 이후)는 자이로·가속도·자력계, 그 전은 자세만. 좌우 바퀴 엔코더, VRS GPS 속도. CAN 없음 | 서울 강남·여의도, 판교 15.7 km 등 도심 시퀀스 다수 (Toyota Prius) | 사이트에서 tar로 받음. 라이선스 표기 없음 (KAIST IRAP 연구실) | ★★★ **국내 도로 가감속을 얻는 가장 현실적인 경로.** 엔코더로 속도, IMU로 yaw |
| AI Hub [자율주행 가상센서 시뮬레이션](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71448) | GPS, IMU (txt) | **시뮬레이션** | 내국인 신청 | ✕ 실주행 아님 |
| AI Hub [자율주행 고장진단](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71347) | 모터 전류·진동(STFT 이미지), 배터리 셀 전압. 메타데이터에 주행 속도 | 아이오닉, 코나, 니로 | 내국인만 신청 | ✕ 주행 신호 시계열 아님 |
| AI Hub 상용 자율주행차 야간 도심도로, 승용 자율주행차 주간 도심도로 | 센서 원본, 위치, 차량 속성(속도 등), 인지·판단·제어 결과 | 국내 도심 | 내국인 신청 | ? 영상 프레임 단위로 보임. 명세서에서 연속 IMU/속도 열이 있는지 확인 필요 |
| 교통안전공단 DTG (디지털 운행기록계) | 속도, 방위각, 가속도, 주행거리, 1초 단위 | 사업용 버스·화물차 | 공개 여부 확인 안 됨 (eTAS 내부, 기업 협업 사례만) | ✕/? 1 Hz라 피처에는 부족. 버스 승객 관점 통계로는 흥미로움 |
| 공공데이터포털 [서울 V2X 차량 상태 정보](https://www.data.go.kr/data/15102656/openapi.do) | 필드 확인 안 됨, 실시간 API | 서울 | 이용 제한 없음 | ? 필드 확인 필요. 대개 위치·속도 저주기 |
| 고려대 HCRL CAN 데이터 (X-CANIDS 등) | 쏘나타 원시 CAN과 해석된 신호, OBD 질의 기록 | 국내 실차 | 연구 공개 | ★ 보안 연구용. IMU 없음, CAN 신호 이름 해석 참고용 |
| GIST [TimelyTale](https://gistpr.medium.com/gist-scientists-unveil-strategies-to-make-self-driven-vehicles-passenger-friendly-e6d2373ee746) (DOI 10.7910/DVN/CQ8UB0) | 승객의 외부·신체·내부 감각, 설명 요구 | 국내 자율주행 탑승 실험 | Harvard Dataverse 공개 | ? 멀미 라벨 아님. 승객 신체 움직임 센서가 있는지 확인 필요 |
| 42dot Open Dataset | 차선, 다중 카메라 추적 (영상) | 국내 | 신청 | ✕ CAN/IMU 없음 |

### 2.4 멀미 라벨이 있는 데이터 (차량 신호는 없거나 비공개)

| 데이터 | 내용 | 상태 |
|---|---|---|
| [4TU: Visually Induced Motion Sickness Correlates with On-Road Car Sickness](https://data.4tu.nl/datasets/5f54188f-9e47-4ac7-8cf3-2ebb852bdf15) | 22명, MISC · MSSQ · MSAQ (엑셀 27 KB) | CC0. **차량 신호 없음** |
| Talsma 등 2023 실차·시뮬레이터 | 24명, 실차 주행과 같은 경로 시뮬레이터의 MISC | 공개 위치 확인 안 됨. TU Delft에 요청할 만함 |
| 시험로 66회 연구 (SAE 2025) | MS 1~10, 개인 특성, 실내 온습도, 가속도·각속도 | 공개 여부 확인 안 됨 |

## 3. 우리 파이프라인에 넣는 방법 (제안)

새 모듈 `ml/realdata/`를 만들어 데이터셋마다 trip.bin 변환기를 둡니다.

1. **좌표 맞추기**: x 앞, y 왼쪽, z 위, 중력 포함 (`protocol/trip_format.json`). comma2k19와 CANTraj(openpilot)는 대략 앞·오른쪽·아래입니다. 실제 구현은 축을 가정하지 않고 데이터에서 추정합니다 (`ml/realdata/convert.py`).
2. **100 Hz로 맞추기**: 100 Hz 데이터는 그대로 씁니다. 10 Hz(L2D)는 올려서 Oman·Wf용으로만 쓰고, 저크·진동 피처는 신뢰도가 낮다고 표시합니다.
3. **없는 열은 NaN**: CAN이 없으면 G2~G7이 NaN이 됩니다. 학습의 그룹 드롭아웃이 이미 이 경우를 다룹니다.
4. **라벨 입력은 속도와 yaw에서**: a_long = dv/dt, a_lat = v·ω_z를 0.63 Hz로 거릅니다. 라벨과 피처가 서로 다른 입력을 쓰는 지금 구조를 지키기 위해서입니다 (C3).
5. **승객과 실내는 합성**: 탑승마다 MSSQ, 체질, 좌석, 온도, 풍량을 지금처럼 뽑아 붙입니다.
6. **구간 길이**: Oman의 느린 시간상수가 502초라, 15분 이상 이어진 구간만 씁니다. comma2k19는 1분 조각을 경로 순서대로 이어 붙입니다.
7. **용도 나누기**
   - 학습: 시뮬레이터(보정 후) + comma2k19 + CANTraj(승인되면)
   - 보정: KAIST(국내 도심)와 우리 직접 녹화로 `sim_config.json`의 가감속·저크·이벤트·진동 분포를 맞춤 (SPEC T-14)
   - 실제 정확도: 우리가 모은 MISC 기록, 받을 수 있으면 TU Delft 실차 데이터

## 4. 다음 할 일

| 순서 | 할 일 | 비고 |
|---|---|---|
| 1 | comma2k19 한 chunk(약 10 GB)로 변환기 만들고 피처·라벨 분포를 시뮬레이터와 비교 | 완료: [REALDATA.md](REALDATA.md) (신호만 140 MB로 받음) |
| 2 | CANTraj 접근 신청 | 연구 목적 기재, 수동 승인. BATON-Sample은 약관 동의만으로 자동 승인. 변환기 `ml/realdata/baton.py` 준비됨 |
| 3 | KAIST Complex Urban의 urban05 이후 시퀀스 한두 개로 국내 도심 분포 확인 | 사용 조건을 연구실에 확인 |
| 4 | TCar_hyu 올린 사람(기관) 확인 | 한양대라면 CAN 해석과 장시간 원본을 물어볼 수 있음 |
| 5 | TU Delft에 Talsma 2023 실차 데이터 요청 | 실제 MISC 검증용 |
| 6 | AI Hub 도심도로 데이터 명세서에서 연속 속도·IMU 열 확인 | 내국인 신청 필요 |

## 출처

- comma2k19: [Hugging Face](https://huggingface.co/datasets/commaai/comma2k19), [GitHub](https://github.com/commaai/comma2k19), [논문](https://arxiv.org/pdf/1812.05752)
- [CANTraj / DriveDNA-GPS](https://huggingface.co/datasets/HenryYHW/DriveDNA-GPS), [BATON](https://huggingface.co/datasets/HenryYHW/BATON)
- [L2D 소개 (Hugging Face 블로그)](https://huggingface.co/blog/lerobot-goes-to-driving-school)
- [smartphone_in_vehicle_multisensor](https://huggingface.co/datasets/InterestingITS/smartphone_in_vehicle_multisensor), [TCar_hyu](https://huggingface.co/datasets/shchon11/TCar_hyu), [RACECAR_DATA](https://huggingface.co/datasets/madhurbehl/RACECAR_DATA)
- [nuScenes 논문](https://arxiv.org/pdf/1903.11027), [Foxglove nuScenes CAN 설명](https://foxglove.dev/blog/visualizing-nuscenes-data-with-foxglove-studio)
- [A2D2](https://www.a2d2.audi/en/), [A2D2 논문](https://arxiv.org/abs/2004.06320)
- PVS: [ETLNet 논문의 설명](https://arxiv.org/pdf/2412.04990), [프로젝트 페이지](https://github.imc.re/jefmenegazzo/Intelligent-Vehicle-Perception-Based-on-Inertial-Sensing-and-Artificial-Intelligence)
- [Reconstructed Roundabout Driving Dataset](https://ieee-dataport.org/node/4458), [KIT Reference Driving Scenarios](https://b2find.eudat.eu/dataset/54a2e910-4744-520b-8db0-82d40a99e68e)
- [KAIST Complex Urban 데이터 형식](https://sites.google.com/view/complex-urban-dataset/format), [논문](https://ar5iv.labs.arxiv.org/html/1803.06121)
- AI Hub: [가상센서 시뮬레이션](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71448), [고장진단](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71347)
- DTG: [보안뉴스](https://m.boannews.com/html/detail.html?idx=97898), [딜사이트](https://dealsite.co.kr/articles/70368)
- [서울 V2X 차량 상태 정보 (공공데이터포털)](https://www.data.go.kr/data/15102656/openapi.do)
- [HCRL CAN 데이터](https://sites.google.com/hksecurity.net/hcrl/Datasets/can-signal-extraction-and-translation-dataset), [42dot 데이터셋 기사](https://www.smarttoday.co.kr/ko-kr/articles/23517)
- TimelyTale: [GIST 소개](https://gistpr.medium.com/gist-scientists-unveil-strategies-to-make-self-driven-vehicles-passenger-friendly-e6d2373ee746)
- 멀미 라벨: [4TU 데이터](https://data.4tu.nl/datasets/5f54188f-9e47-4ac7-8cf3-2ebb852bdf15), [Talsma 2023 (TU Delft)](https://repository.tudelft.nl/record/uuid:b7a904d5-f069-4f97-8df5-9e8b7c1d7c1a), [시험로 연구 (SAE)](https://saemobilus.sae.org/papers/a-deep-learning-framework-time-series-prediction-passenger-motion-sickness-based-vehicle-dynamics-data-2025-01-8104)
