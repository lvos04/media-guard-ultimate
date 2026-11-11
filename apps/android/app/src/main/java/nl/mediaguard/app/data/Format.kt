package nl.mediaguard.app.data

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs

private val AMS = ZoneId.of("Europe/Amsterdam")

/** Getallen en datums volgen de taal van de app (ADR-0023); de tijdzone blijft die van MediaGuard. */
private val loc: Locale get() = Taal.huidig.locale

private fun patroon(nl: String, en: String): DateTimeFormatter = DateTimeFormatter.ofPattern(tr(nl, en), loc)

/** Bytes → "1,4 GB" (en-US: "1.4 GB"); 1 GB = 1024³, zoals de web-UI. */
fun formatBytes(bytes: Long?, signed: Boolean = false): String {
    if (bytes == null) return "—"
    val sign = when {
        !signed -> if (bytes < 0) "−" else ""
        bytes > 0 -> "+"
        bytes < 0 -> "−"
        else -> ""
    }
    val b = abs(bytes).toDouble()
    val units = listOf("B", "KB", "MB", "GB", "TB")
    var v = b
    var i = 0
    while (v >= 1024 && i < units.lastIndex) { v /= 1024; i++ }
    val text = if (i == 0 || v >= 100) String.format(loc, "%.0f", v) else String.format(loc, "%.1f", v)
    return "$sign$text ${units[i]}"
}

/** "4 nov 13:01" (en-US: "Nov 4, 1:01 PM") in Nederlandse tijd; UTC-tekst zonder zone wordt als UTC gelezen. */
fun formatTime(iso: String?): String {
    if (iso.isNullOrBlank()) return ""
    val instant = runCatching { Instant.parse(if (iso.endsWith("Z") || iso.contains('+')) iso else iso + "Z") }
        .getOrNull() ?: return iso
    return patroon("d MMM HH:mm", "MMM d, h:mm a").format(instant.atZone(AMS))
}

/** "woensdag 4 november" (en-US: "Wednesday, November 4"). */
fun formatDay(date: LocalDate): String = patroon("EEEE d MMMM", "EEEE, MMMM d").format(date)

/** Een duur als "2u 5m" (en-US: "2h 5m"); leeg zonder bekende duur. */
fun formatDuur(seconds: Long?): String {
    if (seconds == null || seconds <= 0) return ""
    val h = seconds / 3600
    val m = (seconds % 3600) / 60
    return if (h > 0) tr("${h}u ${m}m", "${h}h ${m}m") else "${m}m"
}

/** Resterende tijd als "nog 2u 5m" (en-US: "2h 5m left", zoals de web-UI). */
fun formatEta(seconds: Long?): String = formatDuur(seconds).let { if (it.isEmpty()) "" else tr("nog $it", "$it left") }

fun yesterday(): LocalDate = LocalDate.now(AMS).minusDays(1)

/** De tekst voor de dagelijkse melding: "12 vervangen · 37 gedownload · 3 aangevraagd · AV1 bespaarde 96 GB". */
fun summaryLine(s: DailySummary): String {
    val parts = mutableListOf<String>()
    parts += tr("${s.replaced.size} vervangen", "${s.replaced.size} replaced")
    parts += tr("${s.downloaded.size} gedownload", "${s.downloaded.size} downloaded")
    if (s.requests.isNotEmpty()) parts += tr("${s.requests.size} aangevraagd", "${s.requests.size} requested")
    val saved = if (s.storage.savedBytes > 0) s.storage.savedBytes else s.encoder.savedBytes
    if (saved > 0) parts += tr("AV1 bespaarde ${formatBytes(saved)}", "AV1 saved ${formatBytes(saved)}")
    return parts.joinToString(" · ")
}

/** "8 nov 2026" (en-US: "Nov 8, 2026"), zoals de subregel van Overzicht in de web. */
fun formatDate(date: LocalDate): String = patroon("d MMM yyyy", "MMM d, yyyy").format(date)

fun today(): LocalDate = LocalDate.now(AMS)

/** "14:02" (en-US: "2:02 PM") in Nederlandse tijd, voor tijdlijnen. */
fun formatClock(iso: String?): String {
    if (iso.isNullOrBlank()) return ""
    val instant = runCatching { Instant.parse(if (iso.endsWith("Z") || iso.contains('+')) iso else iso + "Z") }
        .getOrNull() ?: return ""
    return patroon("HH:mm", "h:mm a").format(instant.atZone(AMS))
}

/** Initialen voor de profielknop: "beheerder" → "BE", "Jan de Vries" → "JV". */
fun initials(name: String?): String {
    val parts = name.orEmpty().trim().split(Regex("[\\s._-]+")).filter { it.isNotBlank() }
    return when {
        parts.isEmpty() -> "MG"
        parts.size == 1 -> parts[0].take(2).uppercase(Locale.ROOT)
        else -> (parts.first().take(1) + parts.last().take(1)).uppercase(Locale.ROOT)
    }
}

/** Epoch-ms als "14:02" (vandaag) of "7 nov 14:02" (eerder), in de tijdzone van de telefoon. */
fun formatClockMillis(epochMs: Long, now: Long = System.currentTimeMillis(), zone: ZoneId = ZoneId.systemDefault()): String {
    val t = Instant.ofEpochMilli(epochMs).atZone(zone)
    val sameDay = t.toLocalDate() == Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
    return (if (sameDay) patroon("HH:mm", "h:mm a") else patroon("d MMM HH:mm", "MMM d, h:mm a")).format(t)
}

/** Epoch-ms als "7 nov 14:02" (en-US: "Nov 7, 2:02 PM"), altijd met de dag erbij; voor meldingen. */
fun formatStampMillis(epochMs: Long, zone: ZoneId = ZoneId.systemDefault()): String =
    patroon("d MMM HH:mm", "MMM d, h:mm a").format(Instant.ofEpochMilli(epochMs).atZone(zone))
