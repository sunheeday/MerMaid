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

# 표준 C++만 쓰는 공용 코드 (피처, 이후 정책)
add_library(mmcommon STATIC common/features.cpp)
target_include_directories(mmcommon PUBLIC common)

add_executable(featurize tools/featurize.cpp)
target_link_libraries(featurize mmcommon)

enable_testing()
add_executable(test_features tests/test_features.cpp)
target_link_libraries(test_features mmcommon)
add_test(NAME features COMMAND test_features)
