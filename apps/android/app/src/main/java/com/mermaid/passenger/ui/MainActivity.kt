package com.mermaid.passenger.ui

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.companion.AssociationInfo
import android.companion.AssociationRequest
import android.companion.BluetoothLeDeviceFilter
import android.companion.CompanionDeviceManager
import android.content.Context
import android.content.Intent
import android.content.IntentSender
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.ParcelUuid
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.ActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.IntentCompat
import com.mermaid.passenger.data.Cmd
import com.mermaid.passenger.data.Prefs
import com.mermaid.passenger.data.Repo
import com.mermaid.passenger.protocol.Proto
import com.mermaid.passenger.service.LinkService
import com.mermaid.passenger.service.Presence
import java.util.regex.Pattern

/** 화면 ID (화면정의서 1장) */
enum class Screen { WELCOME, PERMS, REGISTER, PROFILE_ONB, HOME, REGISTER_AGAIN, PROFILE, SETTINGS, DIAG }

class MainActivity : ComponentActivity() {
    /** 화면이 다시 보일 때마다 증가 → 권한 상태 다시 읽기 */
    private val resumeTick = mutableIntStateOf(0)
    /** 멀미 점수 알림을 눌러 들어올 때마다 증가 → 홈의 "지금 멀미 정도" 카드로 이동 */
    val symFocus = mutableIntStateOf(0)
    private var assocDone: ((String?, String?) -> Unit)? = null
    private val assocLauncher = registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { onAssocResult(it) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (savedInstanceState == null && intent?.getBooleanExtra(EXTRA_SYM, false) == true) symFocus.intValue++
        setContent { MerMaidTheme { AppRoot(this, resumeTick.intValue) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.getBooleanExtra(EXTRA_SYM, false)) symFocus.intValue++
    }

    companion object {
        const val EXTRA_SYM = "sym"
    }

    override fun onResume() {
        super.onResume()
        resumeTick.intValue++
        connectIfReady()
        Repo.send(Cmd.RefreshScreen) // 오버레이 권한이 바뀌었을 수 있음
    }

    /** 앱을 열면 즉시 연결 (APP-F-03). 이미 연결돼 있으면 서비스가 무시 */
    fun connectIfReady() {
        val s = Prefs.settings.value
        if (s.onboarded && s.registered && Perms.bt(this)) LinkService.start(this)
    }

    fun finishOnboarding() {
        Prefs.edit { putBoolean(Prefs.K_ONB, true) }
        connectIfReady()
    }

    // ───────── 차량 등록 (APP-03, CompanionDeviceManager)
    fun registerBridge(done: (address: String?, error: String?) -> Unit) {
        assocDone = done
        val cdm = getSystemService(CompanionDeviceManager::class.java)
        // 광고 패킷에 이름과 128비트 UUID가 다 안 들어갈 수 있어 두 조건 중 하나만 맞아도 표시
        val byName = BluetoothLeDeviceFilter.Builder()
            // 이미 본딩된 폰은 광고 이름(MSAgent-xxxx) 대신 저장된 GAP 이름(MSAgent)으로 보인다 → 둘 다 받는다
            .setNamePattern(Pattern.compile(Pattern.quote(Proto.NAME_PREFIX.trimEnd('-')) + "(-.*)?")).build()
        val byService = BluetoothLeDeviceFilter.Builder()
            .setScanFilter(ScanFilter.Builder().setServiceUuid(ParcelUuid(Proto.SERVICE)).build()).build()
        val req = AssociationRequest.Builder().addDeviceFilter(byName).addDeviceFilter(byService).setSingleDevice(false).build()
        cdm.associate(req, object : CompanionDeviceManager.Callback() {
            // Android 13+의 onAssociationPending 기본 구현이 이 함수를 부름
            @Deprecated("Android 12 호환")
            override fun onDeviceFound(intentSender: IntentSender) {
                assocLauncher.launch(IntentSenderRequest.Builder(intentSender).build())
            }

            override fun onFailure(error: CharSequence?) {
                assocDone?.invoke(null, error?.toString() ?: "차를 찾지 못했어요"); assocDone = null
            }
        }, null)
    }

    @SuppressLint("MissingPermission")
    @Suppress("DEPRECATION")
    private fun onAssocResult(r: ActivityResult) {
        val done = assocDone; assocDone = null
        val data = r.data
        if (r.resultCode != RESULT_OK || data == null) { done?.invoke(null, null); return }
        val scan = IntentCompat.getParcelableExtra(data, CompanionDeviceManager.EXTRA_DEVICE, ScanResult::class.java)
        var address = scan?.device?.address
            ?: IntentCompat.getParcelableExtra(data, CompanionDeviceManager.EXTRA_DEVICE, BluetoothDevice::class.java)?.address
        if (address == null && Build.VERSION.SDK_INT >= 33) {
            address = IntentCompat.getParcelableExtra(data, CompanionDeviceManager.EXTRA_ASSOCIATION, AssociationInfo::class.java)
                ?.deviceMacAddress?.toString()?.uppercase()
        }
        if (address == null) { done?.invoke(null, "기기 정보를 받지 못했어요"); return }
        val name = scan?.scanRecord?.deviceName
            ?: (if (Perms.bt(this)) scan?.device?.name else null)
            ?: address
        Prefs.setBridge(address, name)
        Presence.observe(this, address)
        Repo.log('·', "차량 등록: $name")
        done?.invoke(address, null)
    }

    /** 등록 해제 (APP-06): 존재 감시 중지 → CDM 연결 해제 → 서비스 종료 */
    @Suppress("DEPRECATION")
    fun unregisterBridge() {
        val addr = Prefs.settings.value.bridgeAddress ?: return
        Presence.stop(this, addr)
        val cdm = getSystemService(CompanionDeviceManager::class.java)
        try {
            if (Build.VERSION.SDK_INT >= 33) {
                cdm.myAssociations.filter { it.deviceMacAddress?.toString().equals(addr, ignoreCase = true) }
                    .forEach { cdm.disassociate(it.id) }
            } else cdm.disassociate(addr)
        } catch (_: Exception) {}
        LinkService.sendIfRunning(this, LinkService.ACTION_STOP)
        Prefs.setBridge(null, null)
    }

    fun openOverlaySettings() = safeStart(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))

    @SuppressLint("BatteryLife") // 데모용 사이드로드 배포 가정 (요구사항정의서 7장)
    fun openBatterySettings() = safeStart(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))

    fun openAppSettings() = safeStart(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))

    private fun safeStart(i: Intent) { try { startActivity(i) } catch (_: Exception) { openAppSettingsFallback() } }
    private fun openAppSettingsFallback() { try { startActivity(Intent(Settings.ACTION_SETTINGS)) } catch (_: Exception) {} }
}

/** 권한 상태 (APP-F-40) */
object Perms {
    val BT = arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
    private fun granted(c: Context, p: String) = c.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED
    fun bt(c: Context) = BT.all { granted(c, it) }
    fun notif(c: Context) = Build.VERSION.SDK_INT < 33 || granted(c, Manifest.permission.POST_NOTIFICATIONS)
    fun overlay(c: Context) = Settings.canDrawOverlays(c)
    fun battery(c: Context) = c.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(c.packageName)
    fun missing(c: Context) = listOf(bt(c), notif(c), overlay(c), battery(c)).count { !it }
}

@Composable
fun AppRoot(act: MainActivity, tick: Int) {
    val settings by Prefs.settings.collectAsState()
    var screen by rememberSaveable { mutableStateOf(if (settings.onboarded) Screen.HOME else Screen.WELCOME) }
    val sub = screen in setOf(Screen.PROFILE, Screen.SETTINGS, Screen.DIAG, Screen.REGISTER_AGAIN)
    BackHandler(enabled = sub) { screen = Screen.HOME }
    val symFocus = act.symFocus.intValue
    LaunchedEffect(symFocus) { if (symFocus > 0 && settings.onboarded) screen = Screen.HOME }

    Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
        Box(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
            when (screen) {
                Screen.WELCOME -> WelcomeScreen { screen = Screen.PERMS }
                Screen.PERMS -> PermissionsScreen(act, tick) { screen = Screen.REGISTER }
                Screen.REGISTER -> RegisterScreen(act, onboarding = true, onDone = { screen = Screen.PROFILE_ONB })
                Screen.REGISTER_AGAIN -> RegisterScreen(act, onboarding = false, onDone = { act.connectIfReady(); screen = Screen.HOME })
                Screen.PROFILE_ONB -> ProfileScreen(onboarding = true, onBack = null) { act.finishOnboarding(); screen = Screen.HOME }
                Screen.PROFILE -> ProfileScreen(onboarding = false, onBack = { screen = Screen.HOME }) { screen = Screen.HOME }
                Screen.HOME -> HomeScreen(act, tick, symFocus) { screen = it }
                Screen.SETTINGS -> SettingsScreen(act, tick, onBack = { screen = Screen.HOME }) { screen = it }
                Screen.DIAG -> DiagnosticsScreen(onBack = { screen = Screen.HOME })
            }
        }
    }
}
