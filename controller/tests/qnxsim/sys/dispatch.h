// qnxsim (sys/neutrino.h 참고). 이름은 $QNXSIM_DIR/<name> 소켓 (기본 /tmp/qnxsim)
#pragma once
struct name_attach_t {
    int dpp;
    int chid;
    int mntid;
};
extern "C" {
name_attach_t* name_attach(void* dpp, const char* path, unsigned flags);
int name_detach(name_attach_t* attach, unsigned flags);
int name_open(const char* name, int flags);
int name_close(int coid);
}
