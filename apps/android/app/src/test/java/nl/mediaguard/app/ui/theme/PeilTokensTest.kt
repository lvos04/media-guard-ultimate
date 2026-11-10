package nl.mediaguard.app.ui.theme

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest

/**
 * Drifttest (PEIL_MIGRATIE §10.1, §11.2): elke kleur, typografierol en de radius in PeilTokens.kt is gelijk aan de
 * gepinde design/ontwerptaal/tokens.json, licht en donker. Faalt als iemand een kleur met de hand wijzigt of de
 * tokens bijwerkt zonder apps/android/tools/peil_tokens.py te draaien.
 */
class PeilTokensTest {
    private val bestand: File = listOf("../../../design/ontwerptaal/tokens.json", "../../design/ontwerptaal/tokens.json")
        .map(::File).first { it.isFile }
    private val tokens: JsonObject = Json.parseToJsonElement(bestand.readText()).jsonObject

    private fun hex(c: androidx.compose.ui.graphics.Color): String {
        val argb = (c.value shr 32).toLong()
        return "#%06X".format(argb and 0xFFFFFF)
    }

    @Test fun shaIsDePinnedeVersie() {
        val sha = MessageDigest.getInstance("SHA-256").digest(bestand.readBytes()).joinToString("") { "%02x".format(it) }
        assertEquals(sha, PeilTokens.TOKENS_SHA256)
        assertEquals("1.1.0", PeilTokens.VERSIE)
        // dezelfde sha256 als design/ontwerptaal/HERKOMST.md
        assertEquals("9583506d8585ba39486f75d4c27f8e40f7878104674c27e229d10f92013a30e1", sha)
    }

    @Test fun elkeKleurGelijkAanTokensJson() {
        val kleur = tokens["kleur"]!!.jsonObject
        var geteld = 0
        for (modus in listOf("licht", "donker")) {
            val app = PeilTokens.perPad(if (modus == "licht") PeilTokens.Licht else PeilTokens.Donker)
            for ((groep, inhoud) in kleur) {
                if (groep.startsWith("$") || groep == "alias") continue
                for ((naam, t) in inhoud.jsonObject) {
                    if (naam.startsWith("$")) continue
                    val o = t.jsonObject
                    val verwacht = if (modus == "licht") o["\$value"]!!.jsonPrimitive.content
                    else o["\$extensions"]!!.jsonObject["nl.peil"]!!.jsonObject["modes"]!!.jsonObject["donker"]!!.jsonPrimitive.content
                    val pad = "kleur.$groep.$naam"
                    val c = app[pad] ?: throw AssertionError("$pad ontbreekt in PeilTokens.kt")
                    assertEquals("$pad ($modus)", verwacht.uppercase(), hex(c))
                    geteld++
                }
            }
        }
        assertEquals(2 * PeilTokens.perPad(PeilTokens.Licht).size, geteld)
        assertTrue(geteld > 200)
    }

    @Test fun typografieEnRadiusGelijk() {
        val rollen = tokens["typografie"]!!.jsonObject["rol"]!!.jsonObject
        for ((naam, t) in rollen) {
            if (naam.startsWith("$")) continue
            val v = t.jsonObject["\$value"]!!.jsonObject
            val grootte = v["fontSize"]!!.jsonPrimitive.content.removeSuffix("rem").toFloat() * 16
            val r = PeilTokens.rollen.getValue(naam)
            assertEquals(naam, grootte, r.grootteSp, 0.01f)
            assertEquals(naam, v["fontWeight"]!!.jsonPrimitive.int, r.gewicht)
        }
        assertEquals(8f, PeilTokens.RADIUS_DP, 0f)
        assertEquals(44f, PeilTokens.TOUCH_DP, 0f)
    }

    /** Peil §5: geen tekst onder 14 sp, body 16, pagina 24, waarde 30 — en de app-typografie volgt dat. */
    @Test fun appTypografieVolgtPeil() {
        val t = DefaultMgType
        listOf(t.title, t.section, t.h2, t.body, t.bodyStrong, t.small, t.meta, t.kpi).forEach {
            assertTrue("tekst onder 14 sp: ${it.fontSize}", it.fontSize.value >= 14f)
        }
        assertEquals(16f, t.body.fontSize.value, 0f)
        assertEquals(24f, t.title.fontSize.value, 0f)
        assertEquals(30f, t.kpi.fontSize.value, 0f)
        // proportionele cijfers voor grote waarden; vaste breedte alleen via `cijfers`
        assertEquals(null, t.kpi.fontFeatureSettings)
        assertEquals("tnum", t.meta.cijfers.fontFeatureSettings)
    }

    /** Geen violet in de app (violet = goedkeuring), en de oude namen wijzen naar de juiste Peil-tokens. */
    @Test fun oudeNamenWijzenNaarPeil() {
        for (dark in listOf(false, true)) {
            val c = mgColors(dark)
            val p = if (dark) PeilTokens.Donker else PeilTokens.Licht
            assertEquals(p.background, c.bg)
            assertEquals(p.card, c.surface)
            assertEquals(p.foreground, c.text)
            assertEquals(p.mutedForeground, c.faint)
            assertEquals(p.primary, c.accent)
            assertEquals(p.healthy, c.ok)
            assertEquals(p.down, c.bad)
            assertEquals(p.borderStrong, c.lineStrong)
            val waarden = listOf(c.bg, c.surface, c.surface2, c.sunken, c.line, c.lineStrong, c.text, c.muted, c.accent,
                c.ok, c.warn, c.bad, c.info, c.accentSoft, c.okSoft, c.warnSoft, c.badSoft, c.infoSoft, c.neutralSoft)
            assertTrue(waarden.none { it == p.approval || it == p.approvalBg || it == p.risk2 })
        }
    }
}
