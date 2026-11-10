# P4 정책 구현 메모

`policy.cpp`는 PRD §P4 Policy 의사코드를 그대로 옮겼습니다. 상수는 `controller/config/policy.json`과 `thresholds.json`에서 읽습니다(`json_min.hpp`). PRD가 정하지 않아 여기서 정한 부분은 아래와 같습니다.

| 상황 | 동작 |
|---|---|
| 한 주행에 3번 끄면 비활성화 | PRD 표는 "3번 끔", 의사코드는 30초 안의 빠른 끄기만 셈(`quick[d] >= max_quick`). 의사코드를 따름 |
| 수동 켜기와 쿨다운 | 쿨다운은 자동 켜기만 막음. 승객이 직접 켜는 것은 언제나 됨 |
| 확률이 없는 기기 | 위험 낮음(0)으로 봄. 켜지지 않고, 켜져 있으면 6주기 뒤 꺼짐 |
| HUD 연결 끊김, 폰 해제 | 그 기기는 꺼진 것으로 표시. 이번 주행의 끄기 횟수와 비활성화는 유지 |
| 주행 끝 민감도 복귀 | 이번 주행에 등록된 적 있는 폰의 프로필만 |
| 자리표시 문턱, feature_order 버전 불일치 | 설정 읽기에서 예외 (시작 거부) |

영구 상태(허용도 카운터 n, 프로필)는 `Policy`가 들고 있고, 파일 저장은 P4 프로세스(S6)가 `take_dirty_*()`로 바뀐 키를 받아 합니다.

확인 방법: `ctest --test-dir build` (AC-09 4가지 경우 포함), `python3 -m pytest ml/tests/test_policy_cpp.py` (Python 재생과 같은 ON/OFF).
