package com.mermaid.passenger.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RingTest {
    @Test fun keepsOrderUntilFull() {
        val r = Ring<Int>(3)
        assertNull(r.last())
        r.add(1); r.add(2)
        assertEquals(listOf(1, 2), r.toList())
        assertEquals(2, r.last())
    }

    @Test fun dropsOldestWhenFull() {
        val r = Ring<Int>(3)
        (1..7).forEach { r.add(it) }
        assertEquals(3, r.size)
        assertEquals(listOf(5, 6, 7), r.toList())
        assertEquals(5, r[0]); assertEquals(7, r.last())
    }

    @Test fun clearResets() {
        val r = Ring<String>(2)
        r.add("a"); r.add("b"); r.add("c"); r.clear()
        assertEquals(emptyList<String>(), r.toList())
        r.add("d")
        assertEquals(listOf("d"), r.toList())
    }

    @Test(expected = IndexOutOfBoundsException::class) fun outOfRange() { Ring<Int>(2).apply { add(1) }[1] }

    @Test(expected = IllegalArgumentException::class) fun zeroCapacity() { Ring<Int>(0) }
}

class RateMeterTest {
    @Test fun oneHz() {
        val m = RateMeter(windowMs = 10_000)
        for (i in 0..9) m.mark(i * 1000L)
        assertEquals(1.0, m.hz(9000)!!, 1e-9)
    }

    @Test fun tenHz() {
        val m = RateMeter(windowMs = 10_000)
        for (i in 0..50) m.mark(i * 100L)
        assertEquals(10.0, m.hz(5000)!!, 1e-9)
    }

    @Test fun needsTwoSamples() {
        val m = RateMeter()
        assertNull(m.hz(0))
        m.mark(100)
        assertNull(m.hz(100))
    }

    @Test fun oldSamplesLeaveWindow() {
        val m = RateMeter(windowMs = 10_000)
        for (i in 0..9) m.mark(i * 1000L)         // 1 Hz for 10 s
        for (i in 1..20) m.mark(9000 + i * 200L)  // then 5 Hz for 4 s
        // at t = 13 000 the window is 3 000..13 000 → 1 Hz samples at 3..9 s and 5 Hz after
        val hz = m.hz(13_000)!!
        assertTrue("$hz", hz > 1.0 && hz < 5.0)
        // long after, only the 5 Hz stretch remains
        assertEquals(5.0, m.hz(19_100)!!, 1e-6)
        // and after everything ages out, no rate
        assertNull(m.hz(60_000))
        assertEquals(0, m.count(60_000))
    }

    @Test fun sameTimestampIsNotInfinite() {
        val m = RateMeter()
        m.mark(500); m.mark(500)
        assertNull(m.hz(500))
    }
}

class ThrottleTest {
    @Test fun tenHzLimit() {
        val t = Throttle(100)
        assertTrue(t.pass(0))
        assertFalse(t.pass(50))
        assertFalse(t.pass(99))
        assertTrue(t.pass(100))
        assertFalse(t.pass(150))
        assertTrue(t.pass(250))
    }

    @Test fun twentyHzInputGivesTenHzOutput() {
        val t = Throttle(100)
        val passed = (0 until 200).count { t.pass(it * 50L) } // 10 s at 20 Hz
        assertEquals(100, passed)
    }

    @Test fun resetAllowsImmediately() {
        val t = Throttle(100)
        t.pass(0); t.reset()
        assertTrue(t.pass(10))
    }
}

class FixTest {
    @Test fun sendableNeedsSpeedAndAccuracy() {
        assertTrue(Fix(1.0, 2.0).sendable)
        assertFalse(Fix(null, 2.0).sendable)
        assertFalse(Fix(1.0, null).sendable)
        assertFalse(Fix(Double.NaN, 2.0).sendable)
    }
}

class SymHistoryTest {
    @Test fun sameDriveAppends() {
        var d = SymHistory.add(emptyList(), 100, SymPoint(110, 2))
        d = SymHistory.add(d, 100, SymPoint(120, 5))
        assertEquals(1, d.size)
        assertEquals(listOf(2, 5), d[0].points.map { it.misc })
        assertEquals(5, d[0].max)
        assertEquals(3.5, d[0].avg!!, 1e-9)
    }

    @Test fun newDriveStartsNew() {
        var d = SymHistory.add(emptyList(), 100, SymPoint(110, 2))
        d = SymHistory.add(d, 200, SymPoint(210, 1))
        assertEquals(listOf(100L, 200L), d.map { it.start })
    }

    @Test fun keepsLastDrivesAndPoints() {
        var d = emptyList<SymDrive>()
        for (drive in 1..5L) for (i in 1..4) d = SymHistory.add(d, drive, SymPoint(drive * 100 + i, i), maxDrives = 3, maxPoints = 2)
        assertEquals(listOf(3L, 4L, 5L), d.map { it.start })
        assertEquals(listOf(3, 4), d.last().points.map { it.misc })
    }

    @Test fun jsonRoundTrip() {
        var d = SymHistory.add(emptyList(), 100, SymPoint(110, 2))
        d = SymHistory.add(d, 200, SymPoint(210, 10))
        assertEquals(d, SymHistory.fromJson(SymHistory.toJson(d)))
    }

    @Test fun brokenJsonIsEmpty() {
        assertEquals(emptyList<SymDrive>(), SymHistory.fromJson("{not json"))
        // bad scores are dropped, good ones kept
        val d = SymHistory.fromJson("""[{"start":1,"pts":[[5,3],[6,11],[7,-1],"x"]}]""")
        assertEquals(listOf(SymPoint(5, 3)), d.single().points)
    }

    @Test fun emptyDriveStats() {
        val e = SymDrive(1, emptyList())
        assertNull(e.max); assertNull(e.avg)
    }
}
