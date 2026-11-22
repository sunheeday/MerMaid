# 화면 UI 문서 (탑승객 앱 · 차량 디스플레이 · HUD)

| 문서 | 파일 | 내용 |
|---|---|---|
| 요구사항정의서 MM-REQ-001 v0.1 | [MerMaid_요구사항정의서_v0.1.pdf](MerMaid_요구사항정의서_v0.1.pdf) | 탑승객 앱 Android 재정의(차에 타면 자동 연결 · 백그라운드 · 타 앱 위 모션 큐), 기능 APP-F / DSP-F / HUD-F, 비기능 NFR, BLE·메시지·권한, 확인 필요 사항, 데모 추적 |
| 화면정의서 MM-UI-001 v0.2 | [MerMaid_화면정의서_v0.2.pdf](MerMaid_화면정의서_v0.2.pdf) | Galaxy S22+ 384 × 832 dp 목업: APP-01~07, OVL-01/02, SYS-01, DSP-01(b), HUD-01(b) |

기준: 이 저장소의 `protocol/` v1과 `docs/controller/RF_MESSAGES.md` (RF P5·P6).

구현:
- [`apps/android/`](../../../apps/android/README.md) — 탑승객 앱 (Kotlin, minSdk 31 · targetSdk 35)
- [`apps/hud-web/`](../../../apps/hud-web/README.md) — 차량 디스플레이 · HUD · 탑승객 앱 웹 시연판

## 다시 만들기
원본은 `src/*.html` (Chrome 인쇄). Windows에서:
```
powershell -ExecutionPolicy Bypass -File docs/proposal/ui/build_pdf.ps1
```
파일 이름의 버전은 각 HTML `<title>`에서 읽습니다.
