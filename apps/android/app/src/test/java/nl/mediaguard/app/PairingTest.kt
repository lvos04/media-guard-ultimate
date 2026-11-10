package nl.mediaguard.app

import nl.mediaguard.app.data.Pairing
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingTest {
    @Test fun parsesQrPayload() {
        val p = Pairing.parse("""{"v":1,"url":"mediaguard.example.nl","key":" mgk_abc ","ntfy":{"server":"https://ntfy.sh","topic":"mg-x","enabled":true}}""")!!
        assertEquals("https://mediaguard.example.nl/", p.url)
        assertEquals("mgk_abc", p.key)
        assertEquals("mg-x", p.ntfy?.topic)
        assertTrue(p.ntfy!!.enabled)
    }

    @Test fun keepsHttpLanAddress() {
        assertEquals("http://nas.lan:30080/", Pairing.normalizeUrl("http://nas.lan:30080"))
        assertEquals("http://truenas.local/", Pairing.normalizeUrl("http://truenas.local"))
        assertEquals("http://10.0.0.5/", Pairing.normalizeUrl("http://10.0.0.5"))
        assertEquals("http://nas/", Pairing.normalizeUrl("http://nas"))
        // eigen poort op een publiek domein: rechtstreeks op het LAN, niet via de tunnel
        assertEquals("http://nas.example.nl:30080/", Pairing.normalizeUrl("http://nas.example.nl:30080"))
    }

    @Test fun publicAddressAlwaysHttps() {
        assertEquals("https://mediaguard.example.nl/", Pairing.normalizeUrl("http://mediaguard.example.nl"))
        assertEquals("https://mediaguard.example.nl/", Pairing.normalizeUrl("http://mediaguard.example.nl/"))
        val p = Pairing.parse("""{"v":1,"url":"http://mediaguard.example.nl","key":"mgk_abc"}""")!!
        assertEquals("https://mediaguard.example.nl/", p.url)
        assertTrue(Pairing.isPublicHost("8.8.8.8"))
        assertTrue(!Pairing.isPublicHost("172.20.0.1") && !Pairing.isPublicHost("fd00::1"))
    }

    @Test fun rejectsInvalid() {
        assertNull(Pairing.parse("hello"))
        assertNull(Pairing.parse("""{"url":"https://x.nl"}"""))
        assertNull(Pairing.parse("""{"url":"","key":"mgk_x"}"""))
    }
}
