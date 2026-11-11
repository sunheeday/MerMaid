# Mac:  cmake -S controller -B build && cmake --build build && ctest --test-dir build
# Pi:   같은 명령 (QNX 위 clang). QNX 전용 프로세스(P1, P5, P6)는 나중에 추가
cmake_minimum_required(VERSION 3.16)
project(mermaid_controller CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)

# 표준 C++만 쓰는 공용 코드 (P2 피처, P4 정책)
add_library(mmcommon STATIC common/features.cpp common/policy.cpp)
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
else()
  message(STATUS "MM_LIGHTGBM_INCLUDE / MM_LIGHTGBM_LIB not set: skipping P3 (inference) targets")
endif()
