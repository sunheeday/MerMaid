# MerMaid 탑승객 앱 (Android) v0.2

기준 문서: [요구사항정의서](../../docs/proposal/ui/MerMaid_요구사항정의서_v0.1.pdf) (MM-REQ-001) · [화면정의서](../../docs/proposal/ui/MerMaid_화면정의서_v0.2.pdf) (MM-UI-001)
프로토콜: GitHub `sunheeday/MerMaid` `protocol/` v1 (브랜치 `feat/rf-p5-p6`)

차에 타면 앱을 열지 않아도 차량 컨트롤러와 자동으로 연결되어 **백그라운드에 상주**하고, Tier 3 명령이 오면
**YouTube·SNS 등 다른 앱 위에** 화면 가장자리 모션 큐(점)를 그립니다. 대상 기기는 Galaxy S22+ (Android 12~15).

## 빌드 · 설치
**Android Studio (Ladybug 이상)** 에서:

1. Android Studio → `Open` → 이 `apps/android/` 폴더
2. Gradle 동기화 (Gradle 8.9 · AGP 8.7.3 · Kotlin 2.0.21 · JDK 17은 Android Studio 내장 JBR 사용)
   - 명령줄 빌드(macOS): `local.properties`에 `sdk.dir=<Android SDK 절대 경로>`(예: `/Users/<이름>/Library/Android/sdk`)를 적고 이 폴더에서
     `JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ./gradlew :app:assembleDebug`
     → `app/build/outputs/apk/debug/app-debug.apk` (`local.properties`와 APK는 커밋하지 않음)
3. 폰: 설정 → 휴대전화 정보 → 소프트웨어 정보 → 빌드번호 7번 → 개발자 옵션 → USB 디버깅
4. ▶ Run (`app`) → 첫 실행 온보딩: 시작 → 권한 4단계 → 차 찾기(MSAgent) → 멀미 프로필

## 시험 방법
| 무엇 | 어떻게 |
|---|---|
| 브리지 없이 오버레이 확인 | 디버그 빌드 → 홈 → RF 진단 → **데모 모드** (90초 반복: 34초에 큐 ON) → 홈 화면으로 나가 YouTube 실행 |
| fail-safe (NFR-01) | 데모 모드에서 **끊김 재현** → 5초 뒤 점 즉시 사라짐 → 7초 뒤 복구 |
| 실제 컨트롤러 없이 BLE까지 | ESP32 브리지를 PC에 USB로 연결 → 브리지 개발용 USB 입력(`firmware/bridge/README.md`)으로 PC가 Pi 역할: `0\|{"t":"INT","tier":3,"on":true}` |
| 실제 Pi | 브리지 ↔ Pi UART3, Pi에서 `p6_comm`, `p5_devmgr`, `rf_stub --cycle --motion` (docs/controller/RF_MESSAGES.md §6) |
| 탑승 자동 연결 (APP-F-02) | 앱 종료(최근 앱에서 지움) → 브리지 전원 끔 → 1분 뒤 켬 → 앱을 열지 않아도 상주 알림 “차량과 연결됨” |
| 로그 | `adb logcat` 대신 앱의 RF 진단 화면 (최근 300줄, 기기 안에만). **로그 복사**로 전체를 클립보드에 |
| 멀미 점수 (M08 SYM) | 설정 → 주행 기록 수집 동의 켬 → 홈 "지금 멀미 정도"에서 0~10 → RF 진단에 `→ {"t":"SYM","misc":N}`. 주행 중 2분마다 알림. 데모 모드에서도 됨 |

## 구조
```
app/src/main/java/com/mermaid/passenger/
  MerMaidApp.kt              알림 채널(중요도 낮음), 설정 초기화
  protocol/Protocol.kt       UUID·상수·M00~M08 생성/검사, MSSQ-Short 채점·민감도
  data/Prefs.kt              기기 ID·등록 차량·프로필·큐 설정·주행 기록 수집 동의 (SharedPreferences)
  data/Repo.kt               화면이 보는 상태(StateFlow), 화면→서비스 명령, RF 로그
  link/BleLink.kt            GATT 클라이언트: MTU 247 → 서비스 검색 → 알림 구독, Write Without Response 큐
  link/RfSession.kt          HELLO·PROF·SCR, HB 1초, 5초 감시(LOST→안전 OFF), 재연결 백오프, OVR 대기
  link/DemoLink.kt           개발용 가짜 컨트롤러
  service/LinkService.kt     포그라운드 서비스(connectedDevice): 연결·오버레이·상주 알림·화면 상태·멀미 점수 2분 알림
  service/MerMaidCompanionService.kt  CDM 기기 존재 감지 → 탑승 시 서비스 시작
  service/CueTileService.kt  빠른 설정 타일 "모션 큐"
  service/BootReceiver.kt    재부팅 후 존재 감시 재등록
  overlay/CueOverlay.kt      TYPE_APPLICATION_OVERLAY, 터치 통과, 창 alpha ≤ 0.8
  overlay/MotionDotsView.kt  점 배치·이동·페이드 (Choreographer, 120 Hz)
  ui/                        Compose 화면 APP-01~07
```

## 요구사항 대응 (요약)
| 요구사항 | 구현 위치 |
|---|---|
| APP-F-01 차량 등록 | `MainActivity.registerBridge` (CompanionDeviceManager, 이름·서비스 UUID 필터) |
| APP-F-02 탑승 시 자동 연결 | `MerMaidCompanionService.onDeviceAppeared` → `LinkService` |
| APP-F-04 백그라운드 상주 | `LinkService` (FGS connectedDevice, START_STICKY) |
| APP-F-05·06·07 핸드셰이크·HB·재연결 | `RfSession` |
| APP-F-10~16 타 앱 위 모션 큐 | `CueOverlay`, `MotionDotsView` |
| APP-F-20 SCR | `LinkService.readScreen` (화면 켜짐 · 잠금 해제 · 오버레이 권한) |
| APP-F-21·22 알림·타일 조작 | `LinkService.buildNotification`, `CueTileService` |
| APP-F-23 홈 수동 조작 | `Home.kt TierRow` → `Cmd.Override` → `RfSession.requestOverride` |
| APP-F-30~32 프로필 | `Profile.kt`, `Mssq` |
| 멀미 점수 기록 (M08 SYM) | `Home.kt SymptomCard` → `Cmd.Symptom` → `RfSession.sendSymptom`, 2분 알림 `LinkService.updateSymPrompt` |
| NFR-01 fail-safe | `RfSession.watchdog/safeOff` → `CueOverlay.hideNow` |

## 아직 확인하지 못한 것
- **실기 시험 전**입니다. 컴파일은 확인했습니다 (2026-11-19, macOS · Android Studio 내장 JBR 21 · `./gradlew :app:assembleDebug`가 소스 수정 없이 통과). 폰 설치·실행은 아직 하지 않았습니다.
- One UI에서 CDM 존재 감지로 앱이 깨어나는지 (요구사항정의서 8장 #3)
- 브리지 광고 패킷에 이름과 128비트 UUID가 함께 들어가는지 → 등록 필터를 둘 다 걸어 둠
- SCR `att` 정의 변경 (8장 #1), MOT 부호 (#2)
- 멀미 점수(SYM) 입력·2분 알림·알림에서 바로 고르기, 로그 복사는 빌드만 확인했고 실기 확인 전 (2026-11-21, `docs/app/폰시험_체크리스트.md` A8~A11)
