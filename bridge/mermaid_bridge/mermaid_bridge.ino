// mermaid_bridge (v6) — ESP32-S3 BLE ↔ UART 브리지. 설명은 bridge/README.md
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

static const char* SVC_UUID = "cd820001-2f55-4bae-8e0f-8474006c441e";
static const char* RX_UUID  = "cd820002-2f55-4bae-8e0f-8474006c441e";
static const char* TX_UUID  = "cd820003-2f55-4bae-8e0f-8474006c441e";

NimBLEServer* server; NimBLECharacteristic* txChar;
uint16_t slotHandle[MAX_SLOTS];
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

class ServerCB : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* s, NimBLEConnInfo& info) override {
    int id=findSlot(EMPTY); if(id<0){ s->disconnect(info.getConnHandle()); return; }
    slotHandle[id]=info.getConnHandle(); sendToPi(String(id)+"|CONNECT");
    Serial.printf("[BLE] id %d connected\r\n", id); NimBLEDevice::startAdvertising(); }
  void onDisconnect(NimBLEServer* s, NimBLEConnInfo& info, int reason) override {
    int id=findSlot(info.getConnHandle()); if(id>=0){ slotHandle[id]=EMPTY; sendToPi(String(id)+"|DISCONNECT"); }
    Serial.printf("[BLE] id %d disconnected (reason %d)\r\n", id, reason); NimBLEDevice::startAdvertising(); }
  void onAuthenticationComplete(NimBLEConnInfo& info) override {
    Serial.printf("[BLE] id %d security: encrypted=%d bonded=%d\r\n", findSlot(info.getConnHandle()), info.isEncrypted(), info.isBonded()); }
  void onMTUChange(uint16_t mtu, NimBLEConnInfo& info) override {
    Serial.printf("[BLE] id %d MTU = %u\r\n", findSlot(info.getConnHandle()), mtu); }
};

class RxCB : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* c, NimBLEConnInfo& info) override {
    int id=findSlot(info.getConnHandle()); if(id<0) return;
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
  if(target=="ALL"){ for(int i=0;i<MAX_SLOTS;i++) if(slotHandle[i]!=EMPTY) notifyChunked(slotHandle[i],payload);
    Serial.printf("[BLE] %s -> ALL (%u bytes)\r\n", src, payload.length()); return; }
  if(!isDigit(target[0])) return; int id=target.toInt();
  if(id>=MAX_SLOTS || slotHandle[id]==EMPTY) return;
  if(payload=="DROP"){ server->disconnect(slotHandle[id]); Serial.printf("[BLE] id %d dropped by %s\r\n", id, src); return; }
  int n=notifyChunked(slotHandle[id],payload);
  Serial.printf("[BLE] %s -> id %d (%u bytes, %d chunk)\r\n", src, id, payload.length(), n); }

void setup(){
  Serial.begin(115200); Serial1.setRxBufferSize(4096); Serial1.begin(115200, SERIAL_8N1, PI_RX, PI_TX);
  for(int i=0;i<MAX_SLOTS;i++) slotHandle[i]=EMPTY; delay(500);
  NimBLEDevice::init("MSAgent");
  NimBLEAddress a=NimBLEDevice::getAddress(); const uint8_t* b=a.getBase()->val;  // NimBLE 2.x
  char name[16]; snprintf(name, sizeof name, "MSAgent-%02X%02X", b[1], b[0]);
  NimBLEDevice::setDeviceName(name);
  NimBLEDevice::setMTU(247);
  NimBLEDevice::setSecurityAuth(true,false,true); NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
  server=NimBLEDevice::createServer(); server->setCallbacks(new ServerCB());
  NimBLEService* svc=server->createService(SVC_UUID);
  NimBLECharacteristic* rx=svc->createCharacteristic(RX_UUID, NIMBLE_PROPERTY::WRITE_NR|NIMBLE_PROPERTY::WRITE_ENC);
  rx->setCallbacks(new RxCB());
  txChar=svc->createCharacteristic(TX_UUID, NIMBLE_PROPERTY::NOTIFY|NIMBLE_PROPERTY::READ_ENC); svc->start();
  NimBLEAdvertising* adv=NimBLEDevice::getAdvertising(); adv->setName(name); adv->addServiceUUID(SVC_UUID); adv->start();
  Serial.printf("BLE bridge v6 ready, name %s, stored bonds %d\r\n", name, NimBLEDevice::getNumBonds());
}

void loop(){
  while(Serial1.available()){ char c=Serial1.read(); if(c=='\n'){ piLines++; handleLine(fromPi,"Pi"); fromPi=""; } else if(c!='\r') fromPi+=c; }
  while(Serial.available()){ char c=Serial.read(); if(c=='\n'){ usbLines++; handleLine(fromUsb,"USB"); fromUsb=""; } else if(c!='\r') fromUsb+=c; }
  if(millis()-lastStat>=10000){ lastStat=millis();
    Serial.printf("[STAT] up %lus, connected %d, Pi lines %lu, USB lines %lu, chunks ok %lu, fail %lu\r\n",
                  millis()/1000, server->getConnectedCount(), piLines, usbLines, chunksOk, chunksFail); }
}
