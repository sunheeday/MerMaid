package com.mermaid.passenger.service

import android.companion.CompanionDeviceService
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.data.Repo

/**
 * 등록된 브리지(MSAgent)가 범위에 들어오거나 나가면 Android가 앱을 깨워 호출한다 (APP-F-02, 08).
 * 앱을 열지 않아도 차에 타면 연결이 시작되는 지점. CDM 등록 앱은 백그라운드에서 포그라운드 서비스를 시작할 수 있다.
 */
class MerMaidCompanionService : CompanionDeviceService() {

    @Deprecated("Android 13+는 AssociationInfo 버전이 이 함수를 부름")
    override fun onDeviceAppeared(address: String) {
        val s = Prefs.settings.value
        if (!s.autoConnect || !address.equals(s.bridgeAddress, ignoreCase = true)) return
        Repo.log('·', "차량 감지 ($address) → 자동 연결")
        LinkService.start(this, if (Repo.serviceRunning) LinkService.ACTION_DEVICE_BACK else LinkService.ACTION_START)
    }

    @Deprecated("Android 13+는 AssociationInfo 버전이 이 함수를 부름")
    override fun onDeviceDisappeared(address: String) {
        LinkService.sendIfRunning(this, LinkService.ACTION_DEVICE_GONE)
    }
}
