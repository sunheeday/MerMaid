// C++ 개인 보정 식(calib.hpp)을 적용 (ml/personal/fit_calib.py apply_calib와 같은 값인지 ml/tests/test_calib.py가 확인)
//   calib_apply < rows.txt
// 입력: 한 줄에 "a b p2 p3 p4 p5 p6". 출력: 줄마다 보정한 p2..p6 (%.17g). 범위 밖 a, b는 그대로 (P3와 같음).
#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>

#include "calib.hpp"

int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream ss(line);
        mm::calib::Calib c;
        mm::Risk p{};
        if (!(ss >> c.a >> c.b)) continue;
        for (double& x : p) ss >> x;
        const mm::Risk q = mm::calib::apply(p, c);
        std::printf("%.17g %.17g %.17g %.17g %.17g\n", q[0], q[1], q[2], q[3], q[4]);
    }
    return 0;
}
