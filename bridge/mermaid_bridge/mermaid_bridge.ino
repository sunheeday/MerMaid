// mermaid_bridge (v8) — ESP32-S3 BLE ↔ UART 브리지. 설명은 bridge/README.md
// v7: 연결되면 브리지가 먼저 보안 요청(페어링)을 보내고, 암호화가 끝난 뒤에야 Pi에 CONNECT를 알린다.
//     v6은 페어링을 시작하게 하는 것이 없어서 (RX가 응답 없는 쓰기뿐 → 거절 에러가 없음) 맥·Chrome·앱의 HELLO가 조용히 버려졌다
// v8: 본딩된 기기가 다시 붙을 때 암호화 완료 알림이 연결 알림보다 먼저 와도 CONNECT를 보낸다.
//     v7은 이 경우 번호 없는 연결로 무시하고 15초 뒤 끊어서, 본딩된 기기의 재연결이 Pi에 전혀 알려지지 않았다
// UUID·광고 이름은 protocol/uuids.json, UART 프레임은 protocol/PROTOCOL.md §1
// 보드: ESP32S3 Dev Module, Flash 16MB, PSRAM Disabled. Arduino-ESP32 3.3.12, NimBLE-Arduino 2.x
// nimconfig.h: CONFIG_BT_NIMBLE_MAX_CONNECTIONS = 3 (현재). 9는 기기 모이면 시험
// 배선: Pi 핀7(GPIO4 TX) → GPIO18 / Pi 핀29(GPIO5 RX) ← GPIO17 / Pi 핀9 GND ↔ GND
#include <NimBLEDevice.h>
#define PI_RX 18
#define PI_TX 17
#define MAX_SLOTS 9
#define EMPTY 0xFFFF
#define MAX_MSG 512
#define SECURE_TIMEOUT_MS 15000  // 연결 후 이 안에 암호화가 안 되면 끊음

static const char* SVC_UUID = "cd820001-2f55-4bae-8e0f-8474006c441e";
static const char* RX_UUID  = "cd820002-2f55-4bae-8e0f-8474006c441e";
static const char* TX_UUID  = "cd820003-2f55-4bae-8e0f-8474006c441e";

NimBLEServer* server; NimBLECharacteristic* txChar;
uint16_t slotHandle[MAX_SLOTS];
bool slotSecure[MAX_SLOTS];          // 암호화 끝남 = Pi에 CONNECT를 보냄
unsigned long slotSince[MAX_SLOTS];  // 연결된 시각 (암호화 시간 제한용)
bool slotClosing[MAX_SLOTS];         // 브리지가 끊는 중 (시간 초과, DROP): 늦게 온 암호화 알림 무시
uint16_t earlySecure = EMPTY;        // 연결 알림보다 암호화 완료 알림이 먼저 온 연결 (본딩된 기기 재연결)
String fromPi, fromUsb;
unsigned long piLines = 0, usbLines = 0, chunksOk = 0, chunksFail = 0, lastStat = 0;

int findSlot(uint16_t h){ for(int i=0;i<MAX_SLOTS;i++) if(slotHandle[i]==h) return i; return -1; }

// 폰→Pi 방향: Pi 선으로 보내고, USB에도 "<< " 접두어로 에코 (맥에서 fake_pi가 읽음)
void sendToPi(const String& s){
  String out=s+"\n";
  Serial1.write((const uint8_t*)out.c_str(), out.length());
  Serial.print("<< "); Serial.print(out);
}

int notifyChunked(uint16_t h, const String& payload){
  String msg=payload+"\n"; uint16_t mtu=server->getPeerMTU(h);
  size_t chunk=(mtu>3)?mtu-3:20; const uint8_t* p=(const uint8_t*)msg.c_str(); size_t left=msg.length(); int n=0;
  while(left>0){ size_t len=left<chunk?left:chunk;
    if(txChar->notify(p,len,h)) chunksOk++; else chunksFail++;
    p+=len; left-=len; n++; delay(5);} return n; }

// 암호화가 끝난 연결을 Pi에 알린다. 이미 알렸거나 브리지가 끊는 중이면 아무것도 안 함
void markSecure(int id){
  if(slotSecure[id] || slotClosing[id]) return;
  slotSecure[id]=true; sendToPi(String(id)+"|CONNECT"); }

// 브리지가 연결을 끊기 시작함. 이후 늦게 온 암호화 알림으로 CONNECT를 보내지 않게 표시
void closeSlot(int id){ slotClosing[id]=true; server->disconnect(slotHandle[id]); }

class ServerCB : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* s, NimBLEConnInfo& info) override {
    uint16_t h=info.getConnHandle();
    int id=findSlot(EMPTY); if(id<0){ s->disconnect(h); return; }
    slotHandle[id]=h; slotSecure[id]=false; slotClosing[id]=false; slotSince[id]=millis();
    // v8: 본딩된 기기는 저장된 키로 바로 암호화되어, 암호화 완료 알림이 이 연결 알림보다 먼저 올 수 있다
    if(info.isEncrypted() || earlySecure==h){
      earlySecure=EMPTY;
      Serial.printf("[BLE] id %d connected, already encrypted (bonded reconnect)\r\n", id);
      markSecure(id); NimBLEDevice::startAdvertising(); return; }
    // 기기가 페어링을 먼저 시작하지 않으므로 브리지가 요청한다 (Just Works)
    bool ok=NimBLEDevice::startSecurity(h);
    Serial.printf("[BLE] id %d connected, security request %s\r\n", id, ok?"sent":"failed"); NimBLEDevice::startAdvertising(); }
  void onDisconnect(NimBLEServer* s, NimBLEConnInfo& info, int reason) override {
    uint16_t h=info.getConnHandle(); if(earlySecure==h) earlySecure=EMPTY;
    int id=findSlot(h);
    if(id>=0){ if(slotSecure[id]) sendToPi(String(id)+"|DISCONNECT"); slotHandle[id]=EMPTY; slotSecure[id]=false; slotClosing[id]=false; }
    Serial.printf("[BLE] id %d disconnected (reason %d)\r\n", id, reason); NimBLEDevice::startAdvertising(); }
  void onAuthenticationComplete(NimBLEConnInfo& info) override {
    uint16_t h=info.getConnHandle();
    int id=findSlot(h);
    Serial.printf("[BLE] id %d security: encrypted=%d bonded=%d\r\n", id, info.isEncrypted(), info.isBonded());
    if(!info.isEncrypted()){ if(id>=0) closeSlot(id); else server->disconnect(h); return; }  // 페어링 실패
    if(id<0){ earlySecure=h; return; }  // 연결 알림보다 먼저 옴 → onConnect에서 CONNECT
    markSecure(id); }
  void onMTUChange(uint16_t mtu, NimBLEConnInfo& info) override {
    Serial.printf("[BLE] id %d MTU = %u\r\n", findSlot(info.getConnHandle()), mtu); }
};

class RxCB : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* c, NimBLEConnInfo& info) override {
    int id=findSlot(info.getConnHandle()); if(id<0 || !slotSecure[id]) return;
    NimBLEAttValue v=c->getValue(); String all((const char*)v.data(), v.length());
    int start=0, lines=0;
    while(start<(int)all.length()){ int nl=all.indexOf('\n',start); if(nl<0) nl=all.length();
      String line=all.substring(start,nl); line.trim();
      if(line.length()>0){ sendToPi(String(id)+"|"+line); lines++; } start=nl+1; }
    Serial.printf("[BLE] id %d -> Pi (%u bytes, %d line)\r\n", id, v.length(), lines); }
};

// Pi(또는 USB)에서 온 한 줄 처리. src: "Pi" 또는 "USB" (로그용)
void handleLine(const String& line, const char* src){
  int bar=line.indexOf('|'); if(bar<=0) return;
  String target=line.substring(0,bar), payload=line.substring(bar+1);
  if(payload.length()>MAX_MSG){ Serial.printf("[BLE] dropped: too long (%u)\r\n", payload.length()); return; }
  if(target=="ALL"){ for(int i=0;i<MAX_SLOTS;i++) if(slotHandle[i]!=EMPTY && slotSecure[i]) notifyChunked(slotHandle[i],payload);
    Serial.printf("[BLE] %s -> ALL (%u bytes)\r\n", src, payload.length()); return; }
  if(!isDigit(target[0])) return; int id=target.toInt();
  if(id>=MAX_SLOTS || slotHandle[id]==EMPTY || !slotSecure[id]) return;
  if(payload=="DROP"){ closeSlot(id); Serial.printf("[BLE] id %d dropped by %s\r\n", id, src); return; }
  int n=notifyChunked(slotHandle[id],payload);
  Serial.printf("[BLE] %s -> id %d (%u bytes, %d chunk)\r\n", src, id, payload.length(), n); }

void setup(){
  Serial.begin(115200); Serial1.setRxBufferSize(4096); Serial1.begin(115200, SERIAL_8N1, PI_RX, PI_TX);
  for(int i=0;i<MAX_SLOTS;i++){ slotHandle[i]=EMPTY; slotSecure[i]=false; slotClosing[i]=false; } delay(500);
  NimBLEDevice::init("MSAgent");
  NimBLEAddress a=NimBLEDevice::getAddress(); const uint8_t* b=a.getBase()->val;  // NimBLE 2.x
  char name[16]; snprintf(name, sizeof name, "MSAgent-%02X%02X", b[1], b[0]);
  NimBLEDevice::setDeviceName(name);
  NimBLEDevice::setMTU(247);
  NimBLEDevice::setSecurityAuth(true,false,true); NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
  server=NimBLEDevice::createServer(); server->setCallbacks(new ServerCB());
  NimBLEService* svc=server->createService(SVC_UUID);
  // WRITE도 허용: 응답 있는 쓰기는 암호화 전이면 '암호화 필요' 에러가 돌아가 기기 쪽에서도 페어링이 시작된다
  NimBLECharacteristic* rx=svc->createCharacteristic(RX_UUID, NIMBLE_PROPERTY::WRITE|NIMBLE_PROPERTY::WRITE_NR|NIMBLE_PROPERTY::WRITE_ENC);
  rx->setCallbacks(new RxCB());
  txChar=svc->createCharacteristic(TX_UUID, NIMBLE_PROPERTY::NOTIFY|NIMBLE_PROPERTY::READ_ENC); svc->start();
  NimBLEAdvertising* adv=NimBLEDevice::getAdvertising(); adv->setName(name); adv->addServiceUUID(SVC_UUID); adv->start();
  Serial.printf("BLE bridge v8 ready, name %s, stored bonds %d\r\n", name, NimBLEDevice::getNumBonds());
}

void loop(){
  for(int i=0;i<MAX_SLOTS;i++)
    if(slotHandle[i]!=EMPTY && !slotSecure[i] && millis()-slotSince[i]>SECURE_TIMEOUT_MS){
      Serial.printf("[BLE] id %d not encrypted in %d s, disconnect\r\n", i, SECURE_TIMEOUT_MS/1000);
      closeSlot(i); slotSince[i]=millis(); }
  while(Serial1.available()){ char c=Serial1.read(); if(c=='\n'){ piLines++; handleLine(fromPi,"Pi"); fromPi=""; } else if(c!='\r') fromPi+=c; }
  while(Serial.available()){ char c=Serial.read(); if(c=='\n'){ usbLines++; handleLine(fromUsb,"USB"); fromUsb=""; } else if(c!='\r') fromUsb+=c; }
  if(millis()-lastStat>=10000){ lastStat=millis();
    Serial.printf("[STAT] up %lus, connected %d, Pi lines %lu, USB lines %lu, chunks ok %lu, fail %lu\r\n",
                  millis()/1000, server->getConnectedCount(), piLines, usbLines, chunksOk, chunksFail); }
}
