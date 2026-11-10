package nl.mediaguard.app.ui

import nl.mediaguard.app.R
import nl.mediaguard.app.data.AttentionItem
import nl.mediaguard.app.ui.screens.deliveryLabel
import nl.mediaguard.app.ui.screens.fileChips
import nl.mediaguard.app.ui.screens.seerrLabel
import nl.mediaguard.app.data.LibraryFile
import nl.mediaguard.app.data.MediaInfo
import nl.mediaguard.app.data.QualityInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Peil-betekenis zonder Compose (ONTWERPTAAL §4.3, §7; PEIL_MIGRATIE §6.2, §10.2). */
class PeilUiTest {
    /** Vaste woorden en iconen: één icoon per betekenis, nooit het pauze-icoon voor onbekend. */
    @Test fun statusWoordenEnIconenZijnVast() {
        assertEquals("Gezond", PeilStatus.Gezond.woord)
        assertEquals(R.drawable.lucide_circle_check, PeilStatus.Gezond.icoon)
        assertEquals("Beperkt", PeilStatus.Beperkt.woord)
        assertEquals(R.drawable.lucide_triangle_alert, PeilStatus.Beperkt.icoon)
        assertEquals("Storing", PeilStatus.Storing.woord)
        assertEquals(R.drawable.lucide_octagon_x, PeilStatus.Storing.icoon)
        assertEquals(R.drawable.lucide_circle_help, PeilStatus.Onbekend.icoon)
        assertEquals(R.drawable.lucide_clock_alert, PeilStatus.Verouderd.icoon)
        assertEquals(R.drawable.lucide_circle_pause, PeilStatus.Gepauzeerd.icoon)
        assertEquals(R.drawable.lucide_link_2_off, PeilStatus.NietGekoppeld.icoon)
        assertEquals(R.drawable.lucide_circle_slash, PeilStatus.NietBeschikbaar.icoon)
        // gestippeld = wat niet (meer) gemeten is
        assertTrue(PeilStatus.Onbekend.tone.dashed && PeilStatus.Verouderd.tone.dashed && PeilStatus.NietGekoppeld.tone.dashed)
        assertTrue(!PeilStatus.Gezond.tone.dashed && !PeilStatus.Storing.tone.dashed)
        // alleen Gezond is groen
        assertEquals(listOf(PeilStatus.Gezond), PeilStatus.entries.filter { it.tone == Tone.Ok })
        assertEquals(PeilStatus.entries.size, PeilStatus.entries.map { it.icoon }.toSet().size)
    }

    @Test fun aandachtPerSoort() {
        assertEquals(AttentionKind.Storing, AttentionKind.of("error"))
        assertEquals(AttentionKind.Beperkt, AttentionKind.of("warning"))
        assertEquals(AttentionKind.Info, AttentionKind.of("info"))
        assertEquals(AttentionKind.Info, AttentionKind.of(null))
        // een vergrendelde bibliotheek of een baan die op ruimte wacht is een voorwaarde (slot), geen storing
        assertEquals(AttentionKind.Voorwaarde, AttentionKind.of(AttentionItem("library.locked", "Bibliotheek nog vergrendeld", "warning")))
        assertEquals(AttentionKind.Voorwaarde, AttentionKind.of(AttentionItem("encoder.paused.gpu", "GPU-baan staat stil", "warning")))
        assertEquals(R.drawable.lucide_lock, AttentionKind.Voorwaarde.icoon)
        // nooit een vinkje voor iets dat openstaat
        assertTrue(AttentionKind.entries.none { it.icoon == R.drawable.lucide_circle_check || it.icoon == R.drawable.lucide_check })
        assertTrue(AttentionKind.entries.none { it.tone == Tone.Ok })
    }

    @Test fun totaalstatusVolgtDeLijstMaarNietDeVoorwaarden() {
        assertEquals(PeilStatus.Gezond, totalStatus(0, emptyList()))
        assertEquals(PeilStatus.Gezond, totalStatus(0, listOf(AttentionItem("system.update_available", "Update", "info"))))
        assertEquals(PeilStatus.Gezond, totalStatus(0, listOf(AttentionItem("library.locked", "Vergrendeld", "warning"))))
        assertEquals(PeilStatus.Beperkt, totalStatus(0, listOf(AttentionItem("encoding.stalled", "Stil", "warning"))))
        assertEquals(PeilStatus.Beperkt, totalStatus(1, emptyList()))
        assertEquals(PeilStatus.Beperkt, totalStatus(0, listOf(AttentionItem("encoding.conflict", "Conflict", "error"))))
    }

    @Test fun banenHebbenHunEigenIcoon() {
        assertEquals(R.drawable.lucide_microchip, Baan.of("gpu").icoon)
        assertEquals(R.drawable.lucide_cpu, Baan.of("CPU").icoon)
        assertEquals(Baan.Anders, Baan.of(""))
    }

    /** Beschikbaar, aangevraagd en geleverd zijn geen gezondheid: nooit groen. */
    @Test fun aanvraaglabelsZijnNeutraal() {
        assertEquals(Kenmerk("Beschikbaar", R.drawable.lucide_check), seerrLabel("beschikbaar", "Beschikbaar"))
        assertEquals(Tone.Strong, seerrLabel("gedeeltelijk", "Gedeeltelijk")!!.soort)
        assertEquals(R.drawable.lucide_loader_circle, seerrLabel("verwerken", "Wordt verwerkt")!!.icoon)
        assertEquals("aangevraagd", seerrLabel("aangevraagd", null)!!.tekst)
        assertNull(seerrLabel(null, null))
        val labels = listOf("beschikbaar", "aangevraagd", "verwerken", "gedeeltelijk", "x").map { seerrLabel(it, it)!! } +
            listOf("delivered", "not_found", "waiting_release", "active", "stopped").map { deliveryLabel(it, it) }
        assertTrue(labels.none { it.soort == Tone.Ok || it.soort == Tone.Warn })
    }

    @Test fun bestandslabelsZijnNeutraal() {
        val f = LibraryFile(1, "S01E01", 1, 1, QualityInfo("WEBDL-1080p", 1080, "web"), MediaInfo("AV1", "SDR", emptyList(), emptyList()),
            emptyList(), 2200, true, true, null)
        assertEquals(listOf("AV1 door MediaGuard", "Doel bereikt"), fileChips(f).map { it.tekst })
        assertTrue(fileChips(f).all { it.soort == Tone.Muted })
        assertEquals(listOf("Kan beter"), fileChips(f.copy(av1ByMediaGuard = false, cutoffMet = false)).map { it.tekst })
    }
}
