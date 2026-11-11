// 설정 파일(policy.json, thresholds.json)용 최소 JSON 읽기. 표준 C++17만 사용.
// 객체, 배열, 숫자, 문자열, true/false/null. 문자열의 \uXXXX는 ASCII 범위만 지원.
// 숫자는 15자리 이하면 정확히 직접 읽고(QNX strtod가 1~2 ULP 틀려서), 그보다 길면 strtod.
#pragma once
#include <algorithm>
#include <cstdlib>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace mm::json {

struct Value {
    enum Type { Null, Bool, Number, String, Array, Object } type = Null;
    bool b = false;
    double num = 0;
    std::string str;
    std::vector<Value> arr;
    std::map<std::string, Value> obj;

    const Value& at(const std::string& key) const {
        if (type != Object) throw std::runtime_error("JSON: not an object (key " + key + ")");
        auto it = obj.find(key);
        if (it == obj.end()) throw std::runtime_error("JSON: missing key " + key);
        return it->second;
    }
    bool has(const std::string& key) const { return type == Object && obj.count(key); }
    double number() const {
        if (type != Number) throw std::runtime_error("JSON: not a number");
        return num;
    }
    int integer() const {
        const double x = number();
        if (x != static_cast<int>(x)) throw std::runtime_error("JSON: not an integer");
        return static_cast<int>(x);
    }
    bool boolean() const {
        if (type != Bool) throw std::runtime_error("JSON: not a bool");
        return b;
    }
    const std::string& string() const {
        if (type != String) throw std::runtime_error("JSON: not a string");
        return str;
    }
};

class Parser {
public:
    explicit Parser(const std::string& s) : s_(s) {}
    Value parse() {
        Value v = value();
        ws();
        if (i_ != s_.size()) fail("trailing characters");
        return v;
    }

private:
    const std::string& s_;
    size_t i_ = 0;

    [[noreturn]] void fail(const char* what) const {
        throw std::runtime_error(std::string("JSON: ") + what + " at offset " + std::to_string(i_));
    }
    void ws() {
        while (i_ < s_.size() && (s_[i_] == ' ' || s_[i_] == '\n' || s_[i_] == '\r' || s_[i_] == '\t')) i_++;
    }
    char peek() {
        ws();
        if (i_ >= s_.size()) fail("unexpected end");
        return s_[i_];
    }
    void expect(char c) {
        if (peek() != c) fail("unexpected character");
        i_++;
    }
    bool literal(const char* w) {
        const std::string word(w);
        if (s_.compare(i_, word.size(), word) == 0) {
            i_ += word.size();
            return true;
        }
        return false;
    }
    Value value() {
        const char c = peek();
        Value v;
        if (c == '{') {
            v.type = Value::Object;
            i_++;
            if (peek() == '}') { i_++; return v; }
            for (;;) {
                if (peek() != '"') fail("expected key");
                std::string k = string_lit();
                expect(':');
                v.obj[k] = value();
                if (peek() == ',') { i_++; continue; }
                expect('}');
                return v;
            }
        }
        if (c == '[') {
            v.type = Value::Array;
            i_++;
            if (peek() == ']') { i_++; return v; }
            for (;;) {
                v.arr.push_back(value());
                if (peek() == ',') { i_++; continue; }
                expect(']');
                return v;
            }
        }
        if (c == '"') {
            v.type = Value::String;
            v.str = string_lit();
            return v;
        }
        if (literal("true")) { v.type = Value::Bool; v.b = true; return v; }
        if (literal("false")) { v.type = Value::Bool; return v; }
        if (literal("null")) return v;
        if (c == '-' || (c >= '0' && c <= '9')) {
            v.type = Value::Number;
            v.num = number_lit();
            return v;
        }
        fail("unexpected character");
    }
    // QNX strtod는 소수 문자열의 일부를 1~2 ULP 다르게 읽는다. 설정 값처럼 유효숫자 15자리 이하,
    // 10의 지수 22 이하인 수는 정수 가수 / 10^k 한 번의 연산으로 정확히 반올림해 읽는다
    // (Clinger fast path). 그 밖의 수만 strtod로.
    double number_lit() {
        const size_t start = i_;
        bool neg = false;
        if (s_[i_] == '-') { neg = true; i_++; }
        unsigned long long mant = 0;
        int digits = 0, exp10 = 0;
        bool any = false;
        auto digit = [&](bool frac) {
            const int d = s_[i_++] - '0';
            any = true;
            if (mant == 0 && d == 0) {  // 앞쪽 0은 유효숫자가 아님
                if (frac) exp10--;
                return;
            }
            if (digits < 19) {
                mant = mant * 10 + static_cast<unsigned long long>(d);
                digits++;
                if (frac) exp10--;
            } else {
                digits++;  // 19자리 초과: 아래에서 strtod로
                if (!frac) exp10++;
            }
        };
        while (i_ < s_.size() && s_[i_] >= '0' && s_[i_] <= '9') digit(false);
        if (i_ < s_.size() && s_[i_] == '.') {
            i_++;
            while (i_ < s_.size() && s_[i_] >= '0' && s_[i_] <= '9') digit(true);
        }
        if (!any) fail("bad number");
        if (i_ < s_.size() && (s_[i_] == 'e' || s_[i_] == 'E')) {
            i_++;
            int sign = 1, e = 0;
            if (i_ < s_.size() && (s_[i_] == '+' || s_[i_] == '-')) sign = s_[i_++] == '-' ? -1 : 1;
            if (i_ >= s_.size() || s_[i_] < '0' || s_[i_] > '9') fail("bad exponent");
            while (i_ < s_.size() && s_[i_] >= '0' && s_[i_] <= '9') e = std::min(e * 10 + (s_[i_++] - '0'), 100000);
            exp10 += sign * e;
        }
        static const double pow10[] = {1e0,  1e1,  1e2,  1e3,  1e4,  1e5,  1e6,  1e7,  1e8,  1e9,  1e10, 1e11,
                                       1e12, 1e13, 1e14, 1e15, 1e16, 1e17, 1e18, 1e19, 1e20, 1e21, 1e22};
        if (digits <= 15 && mant <= (1ULL << 53) && exp10 >= -22 && exp10 <= 22) {
            double x = static_cast<double>(mant);
            x = exp10 < 0 ? x / pow10[-exp10] : x * pow10[exp10];
            return neg ? -x : x;
        }
        return std::strtod(s_.substr(start, i_ - start).c_str(), nullptr);
    }
    std::string string_lit() {
        expect('"');
        std::string out;
        while (i_ < s_.size() && s_[i_] != '"') {
            char c = s_[i_++];
            if (c != '\\') { out += c; continue; }
            if (i_ >= s_.size()) fail("bad escape");
            c = s_[i_++];
            switch (c) {
                case '"': case '\\': case '/': out += c; break;
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'u': {
                    if (i_ + 4 > s_.size()) fail("bad \\u escape");
                    const long cp = std::strtol(s_.substr(i_, 4).c_str(), nullptr, 16);
                    if (cp > 0x7f) fail("non-ASCII \\u escape not supported");
                    out += static_cast<char>(cp);
                    i_ += 4;
                    break;
                }
                default: fail("bad escape");
            }
        }
        expect('"');
        return out;
    }
};

inline Value parse(const std::string& text) { return Parser(text).parse(); }

}  // namespace mm::json
