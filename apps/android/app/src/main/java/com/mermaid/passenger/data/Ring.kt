package com.mermaid.passenger.data

/**
 * 크기가 정해진 링버퍼. 가득 차면 가장 오래된 것부터 버린다 (대시보드 그래프 데이터의 메모리 상한).
 * 스레드 안전하지 않다: Repo가 잠그고 쓴다.
 */
class Ring<T>(val capacity: Int) {
    init { require(capacity > 0) { "capacity > 0" } }

    private val buf = arrayOfNulls<Any?>(capacity)
    private var head = 0   // 가장 오래된 칸
    var size = 0
        private set

    fun add(x: T) {
        if (size < capacity) {
            buf[(head + size) % capacity] = x
            size++
        } else {
            buf[head] = x
            head = (head + 1) % capacity
        }
    }

    @Suppress("UNCHECKED_CAST")
    operator fun get(i: Int): T {
        if (i !in 0 until size) throw IndexOutOfBoundsException("$i / $size")
        return buf[(head + i) % capacity] as T
    }

    fun last(): T? = if (size == 0) null else get(size - 1)

    fun clear() {
        buf.fill(null); head = 0; size = 0
    }

    /** 오래된 것 → 새것 순서의 복사본 */
    fun toList(): List<T> = List(size) { get(it) }
}
