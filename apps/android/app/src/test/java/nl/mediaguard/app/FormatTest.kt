package nl.mediaguard.app

import nl.mediaguard.app.data.DailySummary
import nl.mediaguard.app.data.DownloadedFile
import nl.mediaguard.app.data.ReplacedFile
import nl.mediaguard.app.data.RequestEntry
import nl.mediaguard.app.data.SummaryStorage
import nl.mediaguard.app.data.formatBytes
import nl.mediaguard.app.data.formatTime
import nl.mediaguard.app.data.summaryLine
import nl.mediaguard.app.work.Schedules
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDateTime

class FormatTest {
    @Test fun bytesUseBinaryGb() {
        assertEquals("96,0 GB", formatBytes(96L * 1024 * 1024 * 1024))
        assertEquals("+1,5 GB", formatBytes(1610612736L, signed = true))
        assertEquals("−512 MB", formatBytes(-536870912L))
        assertEquals("—", formatBytes(null))
    }

    @Test fun timeInAmsterdam() {
        val t = formatTime("2026-11-04T12:01:00Z")
        org.junit.Assert.assertTrue(t, t.startsWith("4 nov") && t.endsWith("13:01"))
    }

    @Test fun summaryLineMatchesNotificationFormat() {
        val s = DailySummary(
            replaced = List(12) { ReplacedFile("x") },
            downloaded = List(37) { DownloadedFile("y") },
            requests = List(3) { RequestEntry("z") },
            storage = SummaryStorage(savedBytes = 96L * 1024 * 1024 * 1024),
        )
        assertEquals("12 vervangen · 37 gedownload · 3 aangevraagd · AV1 bespaarde 96,0 GB", summaryLine(s))
    }

    @Test fun dailyDelayIsUntilNextOccurrence() {
        val now = LocalDateTime.of(2026, 11, 8, 7, 30)
        assertEquals(30, Schedules.initialDelayMinutes(8 * 60, now))
        assertEquals(23 * 60 + 30, Schedules.initialDelayMinutes(7 * 60, now))
    }
}
