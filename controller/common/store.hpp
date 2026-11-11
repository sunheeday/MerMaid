// P4 영구 상태 파일: 기기별 허용도 카운터와 승객 프로필 (PRD §11 Q7: 바뀔 때마다 기록, 전원이 꺼져도 깨지지 않게).
//   <dir>/tolerance/<device>.json   {"n": 2}
//   <dir>/profiles/<phone>.json     {"mssq": 12.0, "sens_mssq": 1.0, "sens": 1.15}
// 쓰기는 임시 파일 → fsync → rename → 디렉터리 fsync. 중간에 꺼지면 이전 파일이 그대로 남는다.
// POSIX만 사용 (Mac 테스트와 QNX P4가 같이 쓴다).
#pragma once
#include <string>
#include <vector>

#include "policy.hpp"

namespace mm::store {

void write_atomic(const std::string& path, const std::string& text);  // 실패하면 예외
bool read_file(const std::string& path, std::string& out);            // 없으면 false
void make_dirs(const std::string& dir);                               // mkdir -p

std::string tolerance_json(int n);
std::string profile_json(const Profile& p);
int parse_tolerance(const std::string& text);
Profile parse_profile(const std::string& text);

// 기기 이름이 파일 이름으로 안전한지 (영문, 숫자, - _ . 만, 64자 이하, 점으로 시작 안 함)
bool safe_name(const std::string& id);

class Store {
public:
    explicit Store(std::string dir);
    bool load_tolerance(const std::string& device, int& n) const;
    bool load_profile(const std::string& phone, Profile& p) const;
    void save_tolerance(const std::string& device, int n) const;
    void save_profile(const std::string& phone, const Profile& p) const;
    // Policy에서 바뀐 것만 가져가 저장. 저장한 파일 수
    int save_dirty(Policy& pol) const;

private:
    std::string dir_;
};

}  // namespace mm::store
