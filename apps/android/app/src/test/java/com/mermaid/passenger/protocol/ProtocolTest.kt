package com.mermaid.passenger.protocol

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {

    // ───────── M10 RISK

    @Test fun riskParses() {
        val m = Msg.parse("""{"t":"RISK","p":[0.99,0.86,0.55,0.03,0.00]}""")
        assertTrue(m is Incoming.Risk)
        val r = m as Incoming.Risk
        assertEquals(listOf(0.99, 0.86, 0.55, 0.03, 0.0), r.p)
        assertEquals(0.55, r.atLeast(4), 0.0)
        assertEquals(0.99, r.atLeast(2), 0.0)
        assertEquals(0.0, r.atLeast(6), 0.0)
    }

    @Test fun riskIntegersAreFine() {
        val m = Msg.parse("""{"t":"RISK","p":[1,1,0,0,0]}""") as Incoming.Risk
        assertEquals(listOf(1.0, 1.0, 0.0, 0.0, 0.0), m.p)
    }

    @Test fun riskWrongLengthIgnored() {
        assertNull(Msg.parse("""{"t":"RISK","p":[0.9,0.5,0.1,0.0]}"""))
        assertNull(Msg.parse("""{"t":"RISK","p":[0.9,0.5,0.1,0.0,0.0,0.0]}"""))
        assertNull(Msg.parse("""{"t":"RISK","p":[]}"""))
    }

    @Test fun riskOutOfRangeIgnored() {
        assertNull(Msg.parse("""{"t":"RISK","p":[1.01,0.5,0.1,0.0,0.0]}"""))
        assertNull(Msg.parse("""{"t":"RISK","p":[0.9,-0.1,0.1,0.0,0.0]}"""))
    }

    @Test fun riskBadShapeIgnored() {
        assertNull(Msg.parse("""{"t":"RISK"}"""))
        assertNull(Msg.parse("""{"t":"RISK","p":0.5}"""))
        assertNull(Msg.parse("""{"t":"RISK","p":["a",0.5,0.1,0.0,0.0]}"""))
        assertNull(Msg.parse("""{"t":"RISK","p":[null,0.5,0.1,0.0,0.0]}"""))
        assertNull(Msg.parse("""{"t":"RISK","p":[0.9,0.5"""))
    }

    @Test fun oldMessagesStillParse() {
        assertTrue(Msg.parse("""{"t":"INT","tier":3,"on":true}""") is Incoming.Intervention)
        assertTrue(Msg.parse("""{"t":"HB","seq":7}""") is Incoming.Hb)
        assertNull(Msg.parse("""{"t":"NOPE"}"""))
    }

    // ───────── M09 GPS

    @Test fun gpsFull() {
        val o = JSONObject(Msg.gps(spd = 12.3449, acc = 3.24, brg = 87.54, alt = 45.06))
        assertEquals("GPS", o.getString("t"))
        assertEquals(12.34, o.getDouble("spd"), 1e-9)
        assertEquals(87.5, o.getDouble("brg"), 1e-9)
        assertEquals(3.2, o.getDouble("acc"), 1e-9)
        assertEquals(45.1, o.getDouble("alt"), 1e-9)
        assertEquals(setOf("t", "spd", "brg", "acc", "alt"), o.keySet())
    }

    @Test fun gpsOptionalFieldsOmitted() {
        val o = JSONObject(Msg.gps(spd = 0.0, acc = 10.0))
        assertEquals(setOf("t", "spd", "acc"), o.keySet())
        assertFalse(o.has("lat")); assertFalse(o.has("lon"))
    }

    @Test fun gpsNanOptionalOmitted() {
        val o = JSONObject(Msg.gps(spd = 1.0, acc = 1.0, brg = Double.NaN, alt = Double.NaN))
        assertEquals(setOf("t", "spd", "acc"), o.keySet())
    }

    @Test fun gpsBearingNormalized() {
        assertEquals(350.0, JSONObject(Msg.gps(1.0, 1.0, brg = -10.0)).getDouble("brg"), 1e-9)
        assertEquals(0.0, JSONObject(Msg.gps(1.0, 1.0, brg = 359.97)).getDouble("brg"), 1e-9)
        assertEquals(10.0, JSONObject(Msg.gps(1.0, 1.0, brg = 370.0)).getDouble("brg"), 1e-9)
    }

    @Test fun gpsNegativeClamped() {
        val o = JSONObject(Msg.gps(spd = -0.2, acc = -1.0))
        assertEquals(0.0, o.getDouble("spd"), 1e-9)
        assertEquals(0.0, o.getDouble("acc"), 1e-9)
    }

    @Test fun gpsFitsOneMessage() {
        assertTrue(Msg.gps(99.99, 999.9, 359.9, -9999.9).length < Proto.MAX_MSG)
    }
}
