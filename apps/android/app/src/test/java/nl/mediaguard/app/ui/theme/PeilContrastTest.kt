package nl.mediaguard.app.ui.theme

import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/**
 * Contrast van de kleurparen die de schermen echt tekenen (ADR-0018), in licht én donker, volgens WCAG 2.x:
 * tekst ≥ 4,5:1 (Peil: lopende tekst en de tweede tekstkleur ≥ 7:1), iconen, randen van bediening en
 * grafiekmarkeringen ≥ 3:1 (WCAG 1.4.11). Een nieuwe kleurcombinatie in een component hoort hier bij.
 */
class PeilContrastTest {
    private fun lum(c: Color): Double {
        fun ch(v: Float): Double = if (v <= 0.03928) v / 12.92 else ((v + 0.055) / 1.055).pow(2.4)
        return 0.2126 * ch(c.red) + 0.7152 * ch(c.green) + 0.0722 * ch(c.blue)
    }

    private fun ratio(a: Color, b: Color): Double {
        val la = lum(a); val lb = lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    }

    private data class Paar(val wat: String, val voor: (PeilKleuren) -> Color, val achter: (PeilKleuren) -> Color, val min: Double)

    private val paren = listOf(
        // tekst: inkt en de tweede tekstkleur op alle vlakken waar tekst staat (Peil: ≥ 7:1, beide niveaus)
        Paar("foreground op card", { it.foreground }, { it.card }, 7.0),
        Paar("foreground op background", { it.foreground }, { it.background }, 7.0),
        Paar("foreground op popover", { it.foreground }, { it.popover }, 7.0),
        Paar("foreground op muted", { it.foreground }, { it.muted }, 7.0),
        Paar("muted-foreground op card", { it.mutedForeground }, { it.card }, 7.0),
        Paar("muted-foreground op background", { it.mutedForeground }, { it.background }, 7.0),
        Paar("muted-foreground op popover", { it.mutedForeground }, { it.popover }, 7.0),
        Paar("muted-foreground op muted", { it.mutedForeground }, { it.muted }, 7.0),
        Paar("disabled-foreground op muted (uit-knop)", { it.disabledForeground }, { it.muted }, 4.5),
        // gekozen tab/segment: achtergrondkleur op inkt
        Paar("background op foreground (gekozen tab)", { it.background }, { it.foreground }, 7.0),
        // knoppen
        Paar("primary-foreground op primary", { it.primaryForeground }, { it.primary }, 4.5),
        Paar("destructive op card (gevaarknop)", { it.destructive }, { it.card }, 4.5),
        Paar("destructive-foreground op destructive", { it.destructiveForeground }, { it.destructive }, 4.5),
        Paar("border-strong op card (rand van bediening)", { it.borderStrong }, { it.card }, 3.0),
        Paar("input op card (zoekveld)", { it.input }, { it.card }, 3.0),
        Paar("primary op accent (icoon gekozen nav)", { it.primary }, { it.accent }, 3.0),
        // status: woord in de inkt op het eigen vlak, icoon en rand in de statuskleur
        Paar("healthy-ink op card", { it.healthyInk }, { it.card }, 7.0),
        Paar("healthy op card", { it.healthy }, { it.card }, 3.0),
        Paar("degraded-ink op degraded-bg", { it.degradedInk }, { it.degradedBg }, 7.0),
        Paar("degraded op degraded-bg", { it.degraded }, { it.degradedBg }, 3.0),
        Paar("down-ink op down-bg", { it.downInk }, { it.downBg }, 7.0),
        Paar("down op down-bg", { it.down }, { it.downBg }, 3.0),
        Paar("down op card (mislukt-icoon)", { it.down }, { it.card }, 3.0),
        Paar("info-ink op info-bg", { it.infoInk }, { it.infoBg }, 7.0),
        Paar("info op info-bg", { it.info }, { it.infoBg }, 3.0),
        Paar("info op card", { it.info }, { it.card }, 3.0),
        Paar("unknown-ink op unknown-bg", { it.unknownInk }, { it.unknownBg }, 7.0),
        Paar("unknown op unknown-bg", { it.unknown }, { it.unknownBg }, 3.0),
        Paar("unknown-ink op stale-bg", { it.unknownInk }, { it.staleBg }, 7.0),
        Paar("unknown-ink op stale-hatch (arcering)", { it.unknownInk }, { it.staleHatch }, 4.5),
        Paar("stale op stale-bg", { it.stale }, { it.staleBg }, 3.0),
        Paar("stale-ink op stale-bg (laatst bekende waarde)", { it.staleInk }, { it.staleBg }, 4.5),
        Paar("stale-ink op stale-hatch", { it.staleInk }, { it.staleHatch }, 4.5),
        Paar("gate-ink op gate-bg", { it.gateInk }, { it.gateBg }, 7.0),
        Paar("gate op gate-bg", { it.gate }, { it.gateBg }, 3.0),
        Paar("gate op card", { it.gate }, { it.card }, 3.0),
        // grafiek- en baankleuren: alleen icoon, balk of legendablokje op het kaartvlak (≥ 3:1)
        Paar("domain-lane-gpu op card", { it.domainLaneGpu }, { it.card }, 3.0),
        Paar("domain-lane-cpu op card", { it.domainLaneCpu }, { it.card }, 3.0),
        Paar("chart-1 op card", { it.chart1 }, { it.card }, 3.0),
        Paar("chart-2 op card", { it.chart2 }, { it.card }, 3.0),
        Paar("chart-other op card", { it.chartOther }, { it.card }, 3.0),
    )

    @Test fun allePareHalenHunMinimum() {
        val fouten = mutableListOf<String>()
        for ((modus, p) in listOf("licht" to PeilTokens.Licht, "donker" to PeilTokens.Donker)) {
            for (paar in paren) {
                val r = ratio(paar.voor(p), paar.achter(p))
                if (r < paar.min) fouten += "%s (%s): %.2f:1 < %.1f:1".format(paar.wat, modus, r, paar.min)
            }
        }
        assertTrue(fouten.joinToString("\n"), fouten.isEmpty())
    }

    /** Waarom het spoor van een voortgangsbalk het kaartvlak is en niet `chart-meter-track` (zie MgProgress). */
    @Test fun cpuBaanOpMeterSpoorIsTeZwak() {
        val r = ratio(PeilTokens.Licht.domainLaneCpu, PeilTokens.Licht.chartMeterTrack)
        assertTrue("verwacht < 3:1, gemeten %.2f".format(r), r < 3.0)
    }
}
