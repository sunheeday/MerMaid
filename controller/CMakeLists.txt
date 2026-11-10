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
