package com.mermaid.passenger.link

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.content.Context
import android.os.Build
import android.os.Handler
import com.mermaid.passenger.protocol.Proto
import java.io.ByteArrayOutputStream

/** 전송 계층: 한 줄(JSON) 단위로 주고받는다. 콜백은 모두 생성 시 받은 Handler 스레드에서 */
interface Transport {
    interface Callback {
        fun onReady(mtu: Int)
        fun onLine(line: String)
        fun onClosed(reason: String)
        fun onRssi(rssi: Int) {}
    }
    val name: String
    fun open()
    fun send(line: String)
    fun readRssi() {}
    fun close()
}

/**
 * ESP32-S3 브리지(MSAgent-xxxx) GATT 클라이언트.
 * 연결 → MTU 247 요청 → 서비스 검색 → TX 알림 구독(CCCD) → onReady. 암호화 특성이라 첫 연결 때 시스템 페어링(Just Works).
 * 쓰기: 브리지(v6)는 Write 하나를 완결된 줄로 처리하므로 메시지를 나누지 않고 한 번에, Write Without Response, 순서대로.
 */
@SuppressLint("MissingPermission") // BLUETOOTH_CONNECT는 LinkService가 시작 전에 확인
class BleLink(
    private val ctx: Context,
    private val address: String,
    override val name: String,
    private val h: Handler,
    private val cb: Transport.Callback,
) : Transport {
    private var gatt: BluetoothGatt? = null
    private var rx: BluetoothGattCharacteristic? = null
    private var mtu = 23
    private var closed = false
    private val inbox = ByteArrayOutputStream()
    private val queue = ArrayDeque<ByteArray>()
    private var writing = false
    private var writeToken = 0

    private companion object { const val BOND_TIMEOUT_MS = 30_000 }

    override fun open() {
        val adapter = ctx.getSystemService(BluetoothManager::class.java)?.adapter
        if (adapter == null) return fail("블루투스 없음")
        if (!adapter.isEnabled) return fail("블루투스 꺼짐")
        val device = try { adapter.getRemoteDevice(address) } catch (e: IllegalArgumentException) { return fail("주소 오류") }
        gatt = device.connectGatt(ctx, false, gattCb, BluetoothDevice.TRANSPORT_LE, BluetoothDevice.PHY_LE_1M_MASK, h)
            ?: return fail("GATT 연결 실패")
    }

    private fun fail(reason: String) { h.post { if (!closed) cb.onClosed(reason) } }

    override fun send(line: String) {
        val data = (line + "\n").toByteArray(Charsets.UTF_8)
        if (data.size > mtu - 3) {
            // 나눠 쓰면 브리지가 각 조각을 따로 된 줄로 보내므로 경고만 (MTU 247이면 생기지 않음)
            com.mermaid.passenger.data.Repo.log('!', "MTU $mtu 보다 긴 메시지 (${data.size} B)")
        }
        queue.addLast(data)
        pump()
    }

    private fun pump() {
        val g = gatt ?: return
        val ch = rx ?: return
        if (writing || queue.isEmpty()) return
        val data = queue.removeFirst()
        writing = true
        val ok = if (Build.VERSION.SDK_INT >= 33) {
            g.writeCharacteristic(ch, data, BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE) == BluetoothStatusCodes.SUCCESS
        } else {
            @Suppress("DEPRECATION")
            run { ch.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE; ch.value = data; g.writeCharacteristic(ch) }
        }
        if (!ok) { // 스택이 바쁨 → 잠시 뒤 다시
            writing = false
            queue.addFirst(data)
            h.postDelayed({ pump() }, 15)
            return
        }
        // onCharacteristicWrite가 오지 않는 경우 대비
        val token = ++writeToken
        h.postDelayed({ if (writing && token == writeToken) { writing = false; pump() } }, 250)
    }

    override fun readRssi() { gatt?.readRemoteRssi() }

    override fun close() {
        closed = true
        queue.clear()
        gatt?.let { try { it.disconnect() } catch (_: Exception) {}; it.close() }
        gatt = null; rx = null
    }

    private fun feed(bytes: ByteArray) {
        for (b in bytes) {
            if (b == '\n'.code.toByte()) {
                val line = inbox.toString(Charsets.UTF_8.name()).trim()
                inbox.reset()
                if (line.isNotEmpty()) cb.onLine(line)
            } else inbox.write(b.toInt())
        }
        if (inbox.size() > Proto.MAX_MSG * 2) inbox.reset()
    }

    /**
     * 브리지(v7~)는 암호화가 끝나야 컨트롤러에 CONNECT를 알리고, 그 전에 쓴 줄은 버린다. 그래서 본딩이 없으면
     * 앱이 먼저 페어링(Just Works, 시스템 확인 창)을 요청하고 끝난 뒤에 MTU·서비스 검색·HELLO로 넘어간다.
     * 이미 본딩된 폰은 바로 진행 (브리지의 보안 요청으로 저장된 키로 암호화).
     */
    private fun awaitBond(g: BluetoothGatt, waitedMs: Int, wasBonding: Boolean) {
        if (closed || gatt !== g) return
        val state = g.device.bondState
        when {
            state == BluetoothDevice.BOND_BONDED -> { if (!g.requestMtu(Proto.MTU)) g.discoverServices() }
            state == BluetoothDevice.BOND_NONE && wasBonding -> { cb.onClosed("페어링 실패 또는 취소"); g.disconnect() }
            waitedMs > BOND_TIMEOUT_MS -> { cb.onClosed("페어링 시간 초과"); g.disconnect() }
            else -> {
                if (state == BluetoothDevice.BOND_NONE && waitedMs == 0) {
                    com.mermaid.passenger.data.Repo.log('·', "처음 연결: 페어링 요청")
                    g.device.createBond()
                }
                h.postDelayed({ awaitBond(g, waitedMs + 300, wasBonding || state == BluetoothDevice.BOND_BONDING) }, 300)
            }
        }
    }

    private val gattCb = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (closed) return
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)
                awaitBond(g, 0, false)
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED || status != BluetoothGatt.GATT_SUCCESS) {
                g.close()
                if (gatt === g) { gatt = null; rx = null }
                cb.onClosed("연결 끊김 (상태 $status)")
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            this@BleLink.mtu = if (status == BluetoothGatt.GATT_SUCCESS) mtu else 23
            g.discoverServices()
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            val svc = g.getService(Proto.SERVICE)
            val rxCh = svc?.getCharacteristic(Proto.RX)
            val txCh = svc?.getCharacteristic(Proto.TX)
            val cccd = txCh?.getDescriptor(Proto.CCCD)
            if (status != BluetoothGatt.GATT_SUCCESS || rxCh == null || txCh == null || cccd == null) {
                cb.onClosed("MerMaid 서비스 없음"); g.disconnect(); return
            }
            rx = rxCh
            g.setCharacteristicNotification(txCh, true)
            val value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            if (Build.VERSION.SDK_INT >= 33) g.writeDescriptor(cccd, value)
            else @Suppress("DEPRECATION") run { cccd.value = value; g.writeDescriptor(cccd) }
        }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) cb.onReady(mtu)
            else { cb.onClosed("알림 구독 실패 ($status)"); g.disconnect() }
        }

        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) {
            if (c.uuid == Proto.TX) feed(value)
        }

        @Deprecated("Android 12 호환")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            // Android 13+는 위의 value 버전이 불림. 12에서만 처리해 두 번 받지 않게
            @Suppress("DEPRECATION")
            if (Build.VERSION.SDK_INT < 33 && c.uuid == Proto.TX) feed(c.value ?: return)
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) {
            writing = false
            pump()
        }

        override fun onReadRemoteRssi(g: BluetoothGatt, rssi: Int, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) cb.onRssi(rssi)
        }
    }
}
