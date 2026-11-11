#include "store.hpp"

#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cctype>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <stdexcept>

#include "json_min.hpp"

namespace mm::store {

static std::runtime_error sys_error(const std::string& what) {
    return std::runtime_error(what + ": " + std::strerror(errno));
}

static void fsync_dir(const std::string& dir) {
    const int fd = ::open(dir.c_str(), O_RDONLY);
    if (fd < 0) return;  // 디렉터리 fsync를 못 하는 파일 시스템도 있다
    ::fsync(fd);
    ::close(fd);
}

void write_atomic(const std::string& path, const std::string& text) {
    const std::string tmp = path + ".tmp";
    const int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) throw sys_error("open " + tmp);
    size_t off = 0;
    while (off < text.size()) {
        const ssize_t w = ::write(fd, text.data() + off, text.size() - off);
        if (w < 0) {
            if (errno == EINTR) continue;
            ::close(fd);
            throw sys_error("write " + tmp);
        }
        off += static_cast<size_t>(w);
    }
    if (::fsync(fd) != 0) {
        ::close(fd);
        throw sys_error("fsync " + tmp);
    }
    ::close(fd);
    if (::rename(tmp.c_str(), path.c_str()) != 0) throw sys_error("rename " + tmp);
    const size_t slash = path.find_last_of('/');
    fsync_dir(slash == std::string::npos ? "." : path.substr(0, slash));
}

bool read_file(const std::string& path, std::string& out) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    std::ostringstream ss;
    ss << f.rdbuf();
    out = ss.str();
    return true;
}

void make_dirs(const std::string& dir) {
    std::string cur;
    std::stringstream ss(dir);
    std::string part;
    if (!dir.empty() && dir[0] == '/') cur = "/";
    while (std::getline(ss, part, '/')) {
        if (part.empty()) continue;
        cur += part;
        if (::mkdir(cur.c_str(), 0755) != 0 && errno != EEXIST) throw sys_error("mkdir " + cur);
        cur += "/";
    }
}

static std::string num(double x) {
    char b[40];
    std::snprintf(b, sizeof b, "%.17g", x);
    return b;
}

std::string tolerance_json(int n) { return "{\"n\": " + std::to_string(n) + "}\n"; }

std::string profile_json(const Profile& p) {
    return "{\"mssq\": " + num(p.mssq) + ", \"sens_mssq\": " + num(p.sens_mssq) + ", \"sens\": " + num(p.sens) + "}\n";
}

int parse_tolerance(const std::string& text) { return json::parse(text).at("n").integer(); }

Profile parse_profile(const std::string& text) {
    const json::Value v = json::parse(text);
    return {v.at("mssq").number(), v.at("sens_mssq").number(), v.at("sens").number()};
}

bool safe_name(const std::string& id) {
    if (id.empty() || id.size() > 64 || id[0] == '.') return false;
    for (char c : id)
        if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_' || c == '.')) return false;
    return true;
}

Store::Store(std::string dir) : dir_(std::move(dir)) {
    make_dirs(dir_ + "/tolerance");
    make_dirs(dir_ + "/profiles");
}

static std::string checked(const std::string& id) {
    if (!safe_name(id)) throw std::runtime_error("unsafe id for a file name: " + id);
    return id;
}

bool Store::load_tolerance(const std::string& device, int& n) const {
    std::string t;
    if (!read_file(dir_ + "/tolerance/" + checked(device) + ".json", t)) return false;
    n = parse_tolerance(t);
    return true;
}

bool Store::load_profile(const std::string& phone, Profile& p) const {
    std::string t;
    if (!read_file(dir_ + "/profiles/" + checked(phone) + ".json", t)) return false;
    p = parse_profile(t);
    return true;
}

void Store::save_tolerance(const std::string& device, int n) const {
    write_atomic(dir_ + "/tolerance/" + checked(device) + ".json", tolerance_json(n));
}

void Store::save_profile(const std::string& phone, const Profile& p) const {
    write_atomic(dir_ + "/profiles/" + checked(phone) + ".json", profile_json(p));
}

int Store::save_dirty(Policy& pol) const {
    int n = 0;
    for (const auto& d : pol.take_dirty_tolerance()) {
        save_tolerance(d, pol.tolerance_count(d));
        n++;
    }
    for (const auto& ph : pol.take_dirty_profiles()) {
        if (const Profile* p = pol.profile(ph)) {
            save_profile(ph, *p);
            n++;
        }
    }
    return n;
}

}  // namespace mm::store
