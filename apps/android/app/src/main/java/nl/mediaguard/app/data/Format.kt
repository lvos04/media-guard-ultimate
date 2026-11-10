package nl.mediaguard.app.data

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs

private val NL = Locale.forLanguageTag("nl-NL")
private val AMS = ZoneId.of("Europe/Amsterdam")

/** Bytes → "1,4 GB" (1 GB = 1024³, zoals de web-UI). */
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
    val text = if (i == 0 || v >= 100) String.format(NL, "%.0f", v) else String.format(NL, "%.1f", v)
    return "$sign$text ${units[i]}"
}

/** "4 nov 13:01" in Nederlandse tijd; UTC-tekst zonder zone wordt als UTC gelezen. */
fun formatTime(iso: String?): String {
    if (iso.isNullOrBlank()) return ""
    val instant = runCatching { Instant.parse(if (iso.endsWith("Z") || iso.contains('+')) iso else iso + "Z") }
        .getOrNull() ?: return iso
    return DateTimeFormatter.ofPattern("d MMM HH:mm", NL).format(instant.atZone(AMS))
}

fun formatDay(date: LocalDate): String =
    DateTimeFormatter.ofPattern("EEEE d MMMM", NL).format(date)

fun formatEta(seconds: Long?): String {
    if (seconds == null || seconds <= 0) return ""
    val h = seconds / 3600
    val m = (seconds % 3600) / 60
    return if (h > 0) "nog ${h}u ${m}m" else "nog ${m}m"
}

fun yesterday(): LocalDate = LocalDate.now(AMS).minusDays(1)

/** De tekst voor de dagelijkse melding: "12 vervangen · 37 gedownload · 3 aangevraagd · AV1 bespaarde 96 GB". */
fun summaryLine(s: DailySummary): String {
    val parts = mutableListOf<String>()
    parts += "${s.replaced.size} vervangen"
    parts += "${s.downloaded.size} gedownload"
    if (s.requests.isNotEmpty()) parts += "${s.requests.size} aangevraagd"
    val saved = if (s.storage.savedBytes > 0) s.storage.savedBytes else s.encoder.savedBytes
    if (saved > 0) parts += "AV1 bespaarde ${formatBytes(saved)}"
    return parts.joinToString(" · ")
}

/** "8 nov 2026" (zoals de subregel van Overzicht in de web). */
fun formatDate(date: LocalDate): String = DateTimeFormatter.ofPattern("d MMM yyyy", NL).format(date)

fun today(): LocalDate = LocalDate.now(AMS)

/** "HH:mm" in Nederlandse tijd, voor tijdlijnen. */
fun formatClock(iso: String?): String {
    if (iso.isNullOrBlank()) return ""
    val instant = runCatching { Instant.parse(if (iso.endsWith("Z") || iso.contains('+')) iso else iso + "Z") }
        .getOrNull() ?: return ""
    return DateTimeFormatter.ofPattern("HH:mm", NL).format(instant.atZone(AMS))
}

/** Initialen voor de profielknop: "beheerder" → "BE", "Jan de Vries" → "JV". */
fun initials(name: String?): String {
    val parts = name.orEmpty().trim().split(Regex("[\\s._-]+")).filter { it.isNotBlank() }
    return when {
        parts.isEmpty() -> "MG"
        parts.size == 1 -> parts[0].take(2).uppercase(NL)
        else -> (parts.first().take(1) + parts.last().take(1)).uppercase(NL)
    }
}

/** Epoch-ms als "14:02" (vandaag) of "7 nov 14:02" (eerder), in de tijdzone van de telefoon. */
fun formatClockMillis(epochMs: Long, now: Long = System.currentTimeMillis(), zone: ZoneId = ZoneId.systemDefault()): String {
    val t = Instant.ofEpochMilli(epochMs).atZone(zone)
    val sameDay = t.toLocalDate() == Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
    return DateTimeFormatter.ofPattern(if (sameDay) "HH:mm" else "d MMM HH:mm", NL).format(t)
}
