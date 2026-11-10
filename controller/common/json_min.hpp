// 설정 파일(policy.json, thresholds.json)용 최소 JSON 읽기. 표준 C++17만 사용.
// 객체, 배열, 숫자, 문자열, true/false/null. 문자열의 \uXXXX는 ASCII 범위만 지원.
// 숫자는 strtod로 읽는다. 설정 값이라 1~2 ULP 차이는 상관없음 (trip 데이터는 바이너리라 해당 없음).
#pragma once
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
            const char* begin = s_.c_str() + i_;
            char* end = nullptr;
            v.type = Value::Number;
            v.num = std::strtod(begin, &end);
            if (end == begin) fail("bad number");
            i_ += static_cast<size_t>(end - begin);
            return v;
        }
        fail("unexpected character");
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
