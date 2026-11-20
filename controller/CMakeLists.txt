# Mac:  cmake -S controller -B build && cmake --build build && ctest --test-dir build
# Pi:   같은 명령 (QNX 위 clang). LightGBM 경로를 주면 P3와 QNX 프로세스 P2~P4도 빌드. P1, P5, P6, rf_stub은 QNX면 항상 빌드
cmake_minimum_required(VERSION 3.16)
project(mermaid_controller CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)

# 공용 코드: P2 피처, P4 정책, P1 LIVE IMU (표준 C++), P4 상태 파일, P1→P7 공유 메모리 링 (POSIX)
add_library(mmcommon STATIC common/features.cpp common/policy.cpp common/store.cpp common/imu.cpp common/sample_ring.cpp)
target_include_directories(mmcommon PUBLIC common)
# 테스트와 도구가 저장소의 설정 파일을 찾는 경로 (Pi의 P4 프로세스는 실행 인자로 받는다)
set(MM_CONFIG_DIR "${CMAKE_CURRENT_SOURCE_DIR}/config")

add_executable(featurize tools/featurize.cpp)
target_link_libraries(featurize mmcommon)

add_executable(policy_replay tools/policy_replay.cpp)
target_link_libraries(policy_replay mmcommon)
target_compile_definitions(policy_replay PRIVATE MM_CONFIG_DIR="${MM_CONFIG_DIR}")

enable_testing()
add_executable(test_features tests/test_features.cpp)
target_link_libraries(test_features mmcommon)
add_test(NAME features COMMAND test_features)

add_executable(test_policy tests/test_policy.cpp)
target_link_libraries(test_policy mmcommon)
target_compile_definitions(test_policy PRIVATE MM_CONFIG_DIR="${MM_CONFIG_DIR}")
add_test(NAME policy COMMAND test_policy)

add_executable(test_store tests/test_store.cpp)
target_link_libraries(test_store mmcommon)
target_compile_definitions(test_store PRIVATE MM_CONFIG_DIR="${MM_CONFIG_DIR}")
add_test(NAME store COMMAND test_store)

add_executable(test_imu tests/test_imu.cpp)
target_link_libraries(test_imu mmcommon)
target_compile_definitions(test_imu PRIVATE MM_CONFIG_DIR="${MM_CONFIG_DIR}")
add_test(NAME imu COMMAND test_imu)

add_executable(test_sample_ring tests/test_sample_ring.cpp)
target_link_libraries(test_sample_ring mmcommon)
add_test(NAME sample_ring COMMAND test_sample_ring)

# RF: P6 통신 코어(comm), P5 기기 관리 코어(devmgr). 표준 C++, 메시지 구조체는 qnx/rf_messages.hpp
add_library(mmrf STATIC common/comm.cpp common/devmgr.cpp)
target_include_directories(mmrf PUBLIC common qnx)
target_link_libraries(mmrf PUBLIC mmcommon)

add_executable(test_comm tests/test_comm.cpp)
target_link_libraries(test_comm mmrf)
add_test(NAME comm COMMAND test_comm)

add_executable(test_devmgr tests/test_devmgr.cpp)
target_link_libraries(test_devmgr mmrf)
add_test(NAME devmgr COMMAND test_devmgr)

add_executable(test_rf_flow tests/test_rf_flow.cpp)
target_link_libraries(test_rf_flow mmrf)
add_test(NAME rf_flow COMMAND test_rf_flow)

# Pi 없이 P5·P6 프로세스를 함께 돌리는 시험 (Mac, Linux): QNX 메시지 전달은 tests/qnxsim이 Unix 소켓으로 흉내,
# UART는 가상 터미널, ESP32는 tests/rf_sim_test.py. 프로세스 코드는 Pi와 같은 qnx/*.cpp. 약 1분 걸림
if(NOT CMAKE_SYSTEM_NAME STREQUAL "QNX")
  find_package(Threads REQUIRED)
  add_library(qnxsim STATIC tests/qnxsim/qnxsim.cpp)
  target_include_directories(qnxsim PUBLIC tests/qnxsim)
  target_link_libraries(qnxsim PUBLIC Threads::Threads)
  foreach(p p5_devmgr p6_comm rf_stub)
    add_executable(${p}_sim qnx/${p}.cpp)
    target_link_libraries(${p}_sim mmrf qnxsim)
  endforeach()
  # P1, P2, P4 (진짜 코드)와 P3 대역(fake_p3, LightGBM 없이)으로 P1→P6 전체를 돌리는 시험
  foreach(p p1_acq p2_feat p4_policy)
    add_executable(${p}_sim qnx/${p}.cpp)
    target_include_directories(${p}_sim PRIVATE qnx)
    target_link_libraries(${p}_sim mmcommon qnxsim)
  endforeach()
  add_executable(fake_p3_sim tests/fake_p3.cpp)
  target_include_directories(fake_p3_sim PRIVATE qnx)
  target_link_libraries(fake_p3_sim mmcommon qnxsim)
  find_package(Python3 COMPONENTS Interpreter)
  if(Python3_FOUND)
    add_test(NAME rf_sim COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/rf_sim_test.py ${CMAKE_CURRENT_BINARY_DIR})
    set_tests_properties(rf_sim PROPERTIES TIMEOUT 240)
    add_test(NAME pipeline_rf_sim COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/pipeline_rf_sim_test.py ${CMAKE_CURRENT_BINARY_DIR})
    set_tests_properties(pipeline_rf_sim PROPERTIES TIMEOUT 240)
    # P1 LIVE를 가짜 IMU로: 보정 → 주행 → P2 → P3 대역 → P4 (약 30초)
    add_test(NAME live_fake_sim COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/live_fake_sim_test.py ${CMAKE_CURRENT_BINARY_DIR})
    set_tests_properties(live_fake_sim PROPERTIES TIMEOUT 120)
  endif()
endif()

# QNX 프로세스 P5, P6과 시험용 대역 rf_stub, P1 수집 (LightGBM 필요 없음). 보드에서만 빌드
if(CMAKE_SYSTEM_NAME STREQUAL "QNX")
  foreach(p p5_devmgr p6_comm rf_stub)
    add_executable(${p} qnx/${p}.cpp)
    target_link_libraries(${p} mmrf)
  endforeach()
  add_executable(p1_acq qnx/p1_acq.cpp)
  target_include_directories(p1_acq PRIVATE qnx)
  target_link_libraries(p1_acq mmcommon)
endif()

# P3 추론 (lib_lightgbm 4.6.0). 경로를 주면 빌드:
#   Pi:  -DMM_LIGHTGBM_INCLUDE=$HOME/mermaid/LightGBM/include -DMM_LIGHTGBM_LIB=$HOME/mermaid/LightGBM/lib_lightgbm.so
#   Mac: pip lightgbm의 lib/lib_lightgbm.dylib + 같은 버전 헤더
set(MM_LIGHTGBM_INCLUDE "" CACHE PATH "LightGBM include directory (contains LightGBM/c_api.h)")
set(MM_LIGHTGBM_LIB "" CACHE FILEPATH "lib_lightgbm shared library")
if(MM_LIGHTGBM_INCLUDE AND MM_LIGHTGBM_LIB)
  add_library(mminfer STATIC common/inference.cpp)
  target_include_directories(mminfer PUBLIC ${MM_LIGHTGBM_INCLUDE})
  target_link_libraries(mminfer PUBLIC mmcommon ${MM_LIGHTGBM_LIB})
  get_filename_component(MM_LIGHTGBM_LIBDIR ${MM_LIGHTGBM_LIB} DIRECTORY)
  add_executable(verify_model tools/verify_model.cpp)
  target_link_libraries(verify_model mminfer)
  set_target_properties(verify_model PROPERTIES BUILD_RPATH ${MM_LIGHTGBM_LIBDIR})
  # QNX 프로세스 P1~P4 (MsgSend / MsgReceive). 보드에서만 빌드
  if(CMAKE_SYSTEM_NAME STREQUAL "QNX")
    foreach(p p2_feat p3_infer p4_policy)
      add_executable(${p} qnx/${p}.cpp)
      target_include_directories(${p} PRIVATE qnx)
      target_link_libraries(${p} mmcommon)
    endforeach()
    target_link_libraries(p3_infer mminfer)
    set_target_properties(p3_infer PROPERTIES BUILD_RPATH ${MM_LIGHTGBM_LIBDIR})
    # 시연용 P3 대역: 정해진 확률로 RISK (controller/bringup/demo_phone.sh)
    add_executable(fake_p3 tests/fake_p3.cpp)
    target_include_directories(fake_p3 PRIVATE qnx)
    target_link_libraries(fake_p3 mmcommon)
  endif()
else()
  message(STATUS "MM_LIGHTGBM_INCLUDE / MM_LIGHTGBM_LIB not set: skipping P3 (inference) targets")
endif()
