package nl.mediaguard.app

import kotlinx.serialization.json.Json
import nl.mediaguard.app.data.LibraryItem
import nl.mediaguard.app.data.LibraryPage
import nl.mediaguard.app.data.LibraryStats
import nl.mediaguard.app.data.initials
import nl.mediaguard.app.ui.Beschikbaarheid
import nl.mediaguard.app.ui.Kenmerk
import nl.mediaguard.app.ui.Tone
import nl.mediaguard.app.ui.screens.ActivityTab
import nl.mediaguard.app.ui.screens.qualityChips
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DesignLogicTest {
    @Test fun initialsLikeTheWeb() {
        assertEquals("BE", initials("beheerder"))
        assertEquals("JV", initials("Jan de Vries"))
        assertEquals("MG", initials(null))
    }

    /** Zelfde woorden als de web; beschikbaarheid is geen gezondheid (Peil, ADR-0016 punt 6): nooit groen of amber. */
    @Test fun libraryChips() {
        fun item(stats: LibraryStats, monitored: Boolean = true, kind: String = "series") =
            LibraryItem(id = 1, title = "x", kind = kind, monitored = monitored, stats = stats)
        assertEquals(listOf(Kenmerk("2 ontbreken", R.drawable.lucide_file_x, Tone.Strong)),
            qualityChips(item(LibraryStats(total = 10, available = 8, missing = 2))))
        assertEquals(listOf(Kenmerk("Compleet", R.drawable.lucide_check), Kenmerk("Kan beter", R.drawable.lucide_trending_up)),
            qualityChips(item(LibraryStats(total = 10, available = 10, cutoffUnmet = 3))))
        assertEquals(listOf(Beschikbaarheid.nietGevolgd()), qualityChips(item(LibraryStats(total = 4), monitored = false)))
        assertEquals(listOf(Kenmerk("Ontbreekt", R.drawable.lucide_file_x, Tone.Strong)),
            qualityChips(item(LibraryStats(total = 1, missing = 1), kind = "movie")))
        assertEquals(listOf(Beschikbaarheid.compleet(), Beschikbaarheid.verwacht()),
            qualityChips(item(LibraryStats(total = 12, available = 10, upcoming = 2))))
        // geen enkel bibliotheeklabel heeft een statussoort (Gezond/Beperkt/Storing)
        val alle = listOf(LibraryStats(total = 10, available = 8, missing = 2), LibraryStats(total = 10, available = 10, cutoffUnmet = 3),
            LibraryStats(total = 12, available = 10, upcoming = 2)).flatMap { qualityChips(item(it)) }
        assertTrue(alle.all { it.soort == Tone.Muted || it.soort == Tone.Strong })
    }

    @Test fun libraryListParsesCoreResponse() {
        val json = """{"page":1,"page_size":30,"total":1,"items":[{"id":5,"title":"Bergpas","year":2025,"kind":"series",
            "media_type":"tv","poster_url":"https://x/p.jpg","size_on_disk":123,"monitored":true,"has_file":1,
            "status_nl":"Doorlopend","stats":{"total":16,"available":14,"missing":2,"upcoming":0,"unmonitored":0,"cutoff_unmet":1}}]}"""
        val page = Json { ignoreUnknownKeys = true }.decodeFromString(LibraryPage.serializer(), json)
        assertEquals(2, page.items.single().stats?.missing)
        assertEquals("tv", page.items.single().mediaType)
    }

    @Test fun activityTabsFromLinks() {
        assertEquals(ActivityTab.Problemen, ActivityTab.of("problemen"))
        assertEquals(ActivityTab.Nu, ActivityTab.of(null))
        assertEquals(ActivityTab.Gebeurd, ActivityTab.of("gebeurd"))
    }
}
