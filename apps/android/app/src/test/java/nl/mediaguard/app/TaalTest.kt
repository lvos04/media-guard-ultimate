package nl.mediaguard.app

import nl.mediaguard.app.data.AppTaal
import nl.mediaguard.app.data.DailySummary
import nl.mediaguard.app.data.DownloadedFile
import nl.mediaguard.app.data.Lane
import nl.mediaguard.app.data.ReplacedFile
import nl.mediaguard.app.data.RunningEncode
import nl.mediaguard.app.data.SummaryStorage
import nl.mediaguard.app.data.Taal
import nl.mediaguard.app.data.formatBytes
import nl.mediaguard.app.data.formatClock
import nl.mediaguard.app.data.formatClockMillis
import nl.mediaguard.app.data.formatDate
import nl.mediaguard.app.data.formatDay
import nl.mediaguard.app.data.formatDuur
import nl.mediaguard.app.data.formatEta
import nl.mediaguard.app.data.formatStampMillis
import nl.mediaguard.app.data.formatTime
import nl.mediaguard.app.data.initials
import nl.mediaguard.app.data.summaryLine
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.ui.AttentionKind
import nl.mediaguard.app.ui.Beschikbaarheid
import nl.mediaguard.app.ui.LibraryKind
import nl.mediaguard.app.ui.PeilStatus
import nl.mediaguard.app.ui.nav.MAIN_TABS
import nl.mediaguard.app.ui.screens.ActivityTab
import nl.mediaguard.app.ui.screens.RequestsTab
import nl.mediaguard.app.ui.screens.laneSummary
import nl.mediaguard.app.ui.screens.recentTitle
import nl.mediaguard.app.ui.screens.seerrLabel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.ZonedDateTime

/**
 * De app in twee talen (ADR-0023): Nederlands is de standaard, Engels (en-US) volgt de taal van Android. Elke test
 * zet de taal na afloop terug, zodat de overige tests en de Nederlandse referentiebeelden ongewijzigd blijven.
 */
class TaalTest {
    @After fun terugNaarNederlands() { Taal.huidig = AppTaal.Nl }

    private fun <T> engels(blok: () -> T): T {
        Taal.huidig = AppTaal.En
        return try { blok() } finally { Taal.huidig = AppTaal.Nl }
    }

    @Test fun nederlandsIsDeStandaard() {
        assertEquals(AppTaal.Nl, Taal.huidig)
        assertEquals("Overzicht", tr("Overzicht", "Overview"))
        assertEquals("Overview", engels { tr("Overzicht", "Overview") })
        assertEquals("Overzicht", tr("Overzicht", "Overview"))
    }

    /** `app_taal` komt uit de resources die Android kiest; alles wat geen Engels is, blijft Nederlands. */
    @Test fun taalUitDeResource() {
        for (code in listOf("en", "EN", " en ", "en-US", "en-GB")) assertEquals(code, AppTaal.En, AppTaal.of(code))
        for (code in listOf("nl", "nl-NL", "de", "fr", "", "  ", null)) assertEquals("$code", AppTaal.Nl, AppTaal.of(code))
        assertEquals("nl-NL", AppTaal.Nl.locale.toLanguageTag())
        assertEquals("en-US", AppTaal.En.locale.toLanguageTag())
    }

    /** Peil §11.1: de vaste statuswoorden liggen in beide talen vast. */
    @Test fun statuswoordenVolgenPeil() {
        assertEquals(listOf("Gezond", "Beperkt", "Storing", "Onbekend", "Verouderd", "Gepauzeerd", "Niet gekoppeld", "Niet beschikbaar"),
            PeilStatus.entries.map { it.woord })
        assertEquals(listOf("Healthy", "Degraded", "Down", "Unknown", "Stale", "Paused", "Not linked", "Not available"),
            engels { PeilStatus.entries.map { it.woord } })
        assertEquals(listOf("Storing", "Waarschuwing", "Wacht op een voorwaarde", "Info"), AttentionKind.entries.map { it.woord })
        assertEquals(listOf("Down", "Warning", "Waiting for a condition", "Info"), engels { AttentionKind.entries.map { it.woord } })
    }

    /** Lijsten en enums worden één keer gemaakt; het woord moet toch meewisselen met de taal. */
    @Test fun tabsEnLabelsWisselenMee() {
        assertEquals(listOf("Overzicht", "Activiteit", "Bibliotheek", "Ruimte", "Aanvragen"), MAIN_TABS.map { it.label })
        assertEquals(listOf("Overview", "Activity", "Library", "Space", "Requests"), engels { MAIN_TABS.map { it.label } })
        assertEquals(listOf("Nu", "Problemen", "Downloads", "Wat is er gebeurd", "Geschiedenis"), ActivityTab.entries.map { it.label })
        assertEquals(listOf("Now", "Problems", "Downloads", "What happened", "History"), engels { ActivityTab.entries.map { it.label } })
        assertEquals(listOf("Ontdekken", "Mijn aanvragen"), RequestsTab.entries.map { it.label })
        assertEquals(listOf("Discover", "My requests"), engels { RequestsTab.entries.map { it.label } })
        assertEquals(listOf("Series", "Anime", "Films"), LibraryKind.entries.map { it.label })
        assertEquals(listOf("Series", "Anime", "Movies"), engels { LibraryKind.entries.map { it.label } })
        assertEquals("Compleet", Beschikbaarheid.compleet().tekst)
        assertEquals("Complete", engels { Beschikbaarheid.compleet().tekst })
        // de sleutels in routes en meldingen zijn geen tekst en blijven gelijk
        assertEquals(ActivityTab.Gebeurd, engels { ActivityTab.of("gebeurd") })
    }

    /**
     * Geen vaste tekst in de schermen (ADR-0023 punt 4): een kop, knop of tekst met een letterlijke tekst in plaats van
     * `tr(…)` of een tweetalig woord staat in en-US in het Nederlands. Alleen woorden die in beide talen gelijk zijn,
     * mogen letterlijk blijven. Zo vond S-P2-620 de kop "Mijn aanvragen" op het scherm Aanvragen.
     *
     * Wat de scan ziet, in alle `.kt`-bestanden onder `ui/`:
     * - de aanroepen [schermAanroepen] (CardHeader, SectionLabel, PageHeader, EmptyState, MgButton, Text, KeyValue,
     *   KpiTile, TimelineEntry, StatusBadge) met een letterlijke tekst als eerste argument;
     * - in die aanroepen de benoemde argumenten [benoemdeTeksten]: `text =`, `title =`, `label =`, `sub =`, `key =`,
     *   `actionLabel =` en `countLabel =` (dus ook StatusBadge(label = …), KeyValue(key = …) en `sub =` van KpiTile);
     * - bij een tekst met `$` alleen het letterlijke deel vóór de eerste `$` (dus "Bekijk $x" telt, "$x encodes" niet).
     *
     * Grenzen (wat de scan NIET ziet, dus niet bewezen is):
     * - andere aanroepen, zoals Icon(contentDescription = …), StatusPanel, Banner of eigen componenten die niet in
     *   de lijst staan, en elk ander positioneel argument dan het eerste (zoals de tweede tekst van TimelineEntry of de
     *   waarde van KeyValue);
     * - andere benoemde argumenten, zoals `statusLabel =`, `reason =` of `value =`;
     * - letterlijke tekst ná de eerste `$` (zoals " encodes · " in de `sub =` van de KpiTile "Nu bezig"), tekst die via
     *   een variabele, `if`/`when` of een functie binnenkomt, en tekst buiten `ui/`;
     * - de tekst na een escape (`\"`, `\n`): de scan leest tot de eerste `"`, `$` of backslash;
     * - de scan is een tekstscan, geen Kotlin-parser: raw strings (`"""`) en tekenliterals worden niet apart behandeld
     *   (op het moment van schrijven komen ze in `ui/` niet voor);
     * - servertekst (bijvoorbeeld de dagregel van MediaGuard) blijft bewust Nederlands (ADR-0023 punt 8).
     */
    @Test fun geenVasteTekstInDeSchermen() {
        val map = listOf("src/main/java/nl/mediaguard/app/ui", "app/src/main/java/nl/mediaguard/app/ui").map(::File).first { it.isDirectory }
        val bestanden = map.walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()
        assertTrue("te weinig schermbestanden gevonden in $map", bestanden.size >= 10)
        val gevonden = bestanden.flatMap { f -> vasteTeksten(f.readText()).filter { it !in inBeideTalenGelijk }.map { "${f.name}: \"$it\"" } }
        assertEquals(emptyList<String>(), gevonden)
        // de kop van de kaart volgt het woord van het tabblad
        assertEquals("My requests", engels { RequestsTab.Mijn.label })
    }

    /** De scan zelf: wat hij moet vinden en wat niet (anders kan [geenVasteTekstInDeSchermen] stil te weinig zien). */
    @Test fun scanOpVasteTekstVindtDeGaten() {
        val bron = """
            KeyValue("Serveradres", m.serverUrl ?: "—")
            MgButton("Bekijk ${'$'}RELEASES", onClick = {})
            MgButton(onClick = {}, text = "Opslaan")
            CardHeader(icon = R.drawable.x, title = "Mijn kaart")
            KpiTile("Wachtrij", "3", Modifier.weight(1f))
            KpiTile(label = "Klaar", value = null)
            TimelineEntry("Vervangen", "tweede argument telt niet")
            EmptyState(tr("Leeg", "Empty"), sub = "Niets te zien")
            Text(text = if (a) "x" else "y")
            Text("${'$'}count", style = s)
            Text(tr("Goed", "Good"), modifier = Modifier.semantics { contentDescription = "(" })
            Text("Na (haakjes)", color = c)
            val f by animateFloatAsState(1f, tween(150), label = "progress")
            Text("%.0f %%")
            Text("Server")
            StatusBadge(PeilStatus.Gepauzeerd, label = "Noodstop actief")
            StatusBadge(status, label = k.tekst, reason = "niet gezien: reason")
            KeyValue(key = "Versie", value = "niet gezien: waarde")
            KpiTile(tr("Nu", "Now"), "2", sub = "kleiner, zelfde kwaliteit")
            KpiTile(tr("Nu", "Now"), "2", sub = "${'$'}{n} encodes · ${'$'}{m} downloads")
            EmptyState(tr("Leeg", "Empty"), actionLabel = "Opnieuw proberen", onAction = {})
            CardHeader(icon = R.drawable.x, title = tr("Let op", "Attention"), count = 1, countLabel = "1 ding wacht")
            item(key = "geen schermtekst")
        """.trimIndent()
        assertEquals(listOf("Serveradres", "Bekijk ", "Opslaan", "Mijn kaart", "Wachtrij", "Klaar", "Vervangen",
            "Niets te zien", "Na (haakjes)", "Noodstop actief", "Versie", "kleiner, zelfde kwaliteit", "Opnieuw proberen",
            "1 ding wacht"), vasteTeksten(bron).filter { it !in inBeideTalenGelijk })
    }

    @Test fun getallenVolgenDeTaal() {
        val gb = 1024L * 1024 * 1024
        assertEquals("96,0 GB", formatBytes(96 * gb))
        assertEquals("96.0 GB", engels { formatBytes(96 * gb) })
        assertEquals("+1.5 GB", engels { formatBytes(1610612736L, signed = true) })
        assertEquals("−512 MB", engels { formatBytes(-536870912L) })
        assertEquals("—", engels { formatBytes(null) })
    }

    @Test fun datumsEnTijdenVolgenDeTaal() {
        // de tijdzone blijft die van MediaGuard (Europe/Amsterdam): 12:01 UTC is 13:01
        val nl = formatTime("2026-11-04T12:01:00Z")
        assertTrue(nl, nl.startsWith("4 nov") && nl.endsWith("13:01"))
        assertEquals("Nov 4, 1:01 PM", engels { formatTime("2026-11-04T12:01:00Z") })
        assertEquals("13:01", formatClock("2026-11-04T12:01:00Z"))
        assertEquals("1:01 PM", engels { formatClock("2026-11-04T12:01:00Z") })

        val dag = LocalDate.of(2026, 11, 7)
        assertEquals("zaterdag 7 november", formatDay(dag))
        assertEquals("Saturday, November 7", engels { formatDay(dag) })
        assertTrue(formatDate(dag), formatDate(dag).startsWith("7 nov") && formatDate(dag).endsWith("2026"))
        assertEquals("Nov 7, 2026", engels { formatDate(dag) })

        val toen = ZonedDateTime.of(2026, 11, 7, 14, 2, 0, 0, ZoneOffset.UTC).toInstant().toEpochMilli()
        val zelfdeDag = toen + 3_600_000
        val dagLater = toen + 24 * 3_600_000
        assertEquals("14:02", formatClockMillis(toen, now = zelfdeDag, zone = ZoneOffset.UTC))
        assertEquals("2:02 PM", engels { formatClockMillis(toen, now = zelfdeDag, zone = ZoneOffset.UTC) })
        assertEquals("Nov 7, 2:02 PM", engels { formatClockMillis(toen, now = dagLater, zone = ZoneOffset.UTC) })
        val stempel = formatStampMillis(toen, ZoneOffset.UTC)
        assertTrue(stempel, stempel.startsWith("7 nov") && stempel.endsWith("14:02"))
        assertEquals("Nov 7, 2:02 PM", engels { formatStampMillis(toen, ZoneOffset.UTC) })
    }

    @Test fun duurEnResterendeTijd() {
        assertEquals("2u 5m", formatDuur(7500))
        assertEquals("2h 5m", engels { formatDuur(7500) })
        assertEquals("9m", formatDuur(540))
        assertEquals("", formatDuur(null))
        assertEquals("", formatDuur(0))
        assertEquals("nog 2u 5m", formatEta(7500))
        assertEquals("nog 9m", formatEta(540))
        assertEquals("2h 5m left", engels { formatEta(7500) })
        assertEquals("9m left", engels { formatEta(540) })
        assertEquals("", engels { formatEta(null) })
    }

    @Test fun baanregelInBeideTalen() {
        val baan = Lane(lane = "gpu", running = 1, slots = 2)
        val bezig = listOf(RunningEncode(lane = "gpu", etaSeconds = 7500), RunningEncode(lane = "gpu", etaSeconds = 540))
        assertEquals("1 van 2 bezig" to "eerstvolgende klaar over 9m", laneSummary(baan, bezig))
        assertEquals("1 of 2 busy" to "next one done in 9m", engels { laneSummary(baan, bezig) })
        assertEquals("uit · gepauzeerd", laneSummary(Lane(lane = "cpu", paused = true), emptyList()).first)
        assertEquals("off · paused", engels { laneSummary(Lane(lane = "cpu", paused = true), emptyList()).first })
    }

    /** De dagmelding gebruikt dezelfde regel; in het Engels met een decimale punt. */
    @Test fun dagregelInHetEngels() {
        val s = DailySummary(replaced = List(12) { ReplacedFile("x") }, downloaded = List(37) { DownloadedFile("y") },
            storage = SummaryStorage(savedBytes = 96L * 1024 * 1024 * 1024))
        assertEquals("12 vervangen · 37 gedownload · AV1 bespaarde 96,0 GB", summaryLine(s))
        assertEquals("12 replaced · 37 downloaded · AV1 saved 96.0 GB", engels { summaryLine(s) })
    }

    /**
     * MediaGuard geeft statusteksten alleen in het Nederlands. Bij een bekende Seerr-status kiest de app in het
     * Engels zelf het woord; een onbekende status toont de tekst van de server ongewijzigd.
     */
    @Test fun seerrStatusInBeideTalen() {
        assertEquals("Beschikbaar", seerrLabel("beschikbaar", "Beschikbaar")?.tekst)
        assertEquals("Available", engels { seerrLabel("beschikbaar", "Beschikbaar")?.tekst })
        assertEquals("Requested", engels { seerrLabel("aangevraagd", "Aangevraagd")?.tekst })
        assertEquals("Processing", engels { seerrLabel("verwerken", "Wordt verwerkt")?.tekst })
        assertEquals("Partial", engels { seerrLabel("gedeeltelijk", "Gedeeltelijk beschikbaar")?.tekst })
        assertEquals("Afgewezen", engels { seerrLabel("afgewezen", "Afgewezen")?.tekst })
        assertEquals(null, engels { seerrLabel(null, null) })
    }

    /**
     * De kaart "Laatste 24 uur": het venster "24h" is een bekende code, dus in het Engels kiest de app zelf de titel.
     * In het Nederlands blijft de tekst van de server staan.
     */
    @Test fun titelVanHetRollendeVensterInBeideTalen() {
        val venster = DailySummary(window = "24h", label = "Laatste 24 uur")
        assertEquals("Laatste 24 uur", recentTitle(venster))
        assertEquals("Last 24 hours", engels { recentTitle(venster) })
        // een serverlabel dat afwijkt van de vaste tekst van de app: in het Nederlands staat precies dat label, in het
        // Engels toch de eigen titel (anders kan de Nederlandse helft van deze test niet falen, V-003)
        val eigenLabel = DailySummary(window = "24h", label = "Laatste 24 uur (server)")
        assertEquals("Laatste 24 uur (server)", recentTitle(eigenLabel))
        assertEquals("Last 24 hours", engels { recentTitle(eigenLabel) })
        assertEquals("Laatste 24 uur", recentTitle(DailySummary(window = "24h")))
        assertEquals("Last 24 hours", engels { recentTitle(DailySummary(window = "24h")) })
        assertEquals("Last 24 hours", engels { recentTitle(null) })
        assertEquals("Yesterday", engels { recentTitle(DailySummary(date = "2026-11-07")) })
    }

    @Test fun waarschuwingEnInitialen() {
        val uit = PhoneState(notificationsAllowed = false)
        assertEquals("Meldingen staan uit voor MediaGuard op deze telefoon", uit.warning(ntfyOn = true))
        assertEquals("Notifications are off for MediaGuard on this phone", engels { uit.warning(ntfyOn = true) })
        // initialen hangen niet van de taal af
        assertEquals("JV", engels { initials("Jan de Vries") })
        assertEquals("MG", engels { initials(null) })
    }

    private companion object {
        /** De schermcomponenten met een zichtbare tekst als eerste argument (zie [geenVasteTekstInDeSchermen]). */
        val schermAanroepen = listOf("CardHeader", "SectionLabel", "PageHeader", "EmptyState", "MgButton", "Text",
            "KeyValue", "KpiTile", "TimelineEntry", "StatusBadge")

        /** De benoemde argumenten met een zichtbare tekst, in elke aanroep uit [schermAanroepen]. */
        val benoemdeTeksten = setOf("text", "title", "label", "sub", "key", "actionLabel", "countLabel")

        /** In het Nederlands en het Engels hetzelfde woord (of geen tekst voor de lezer). */
        val inBeideTalenGelijk = setOf("Downloads", "Upgrades", "Details", "Release", "MediaGuard",
            "https://mediaguard.example.nl", "Audio", "Score", "Server", "App", "In MediaGuard", "Custom formats", "Encodes")

        private val aanroep = Regex("""\b(${schermAanroepen.joinToString("|")})\(""")
        private val benoemd = Regex("""^(\w+)\s*=(?!=)\s*(.*)$""", RegexOption.DOT_MATCHES_ALL)
        private val opmaak = Regex("""%[-#+ 0,(]*\d*(\.\d+)?[a-zA-Z%]""")

        /** Alle letterlijke teksten die de scan in [bron] ziet; alleen met een letter (opmaakpatronen tellen niet). */
        fun vasteTeksten(bron: String): List<String> = aanroep.findAll(bron).flatMap { m ->
            argumenten(bron, m.range.last + 1).mapIndexedNotNull { i, arg ->
                val b = benoemd.find(arg)
                when {
                    b != null -> if (b.groupValues[1] in benoemdeTeksten) letterlijkBegin(b.groupValues[2]) else null
                    i == 0 -> letterlijkBegin(arg)
                    else -> null
                }
            }
        }.filter { tekst -> tekst.replace(opmaak, "").any { it.isLetter() } }.toList()

        /** Het letterlijke deel van een tekst vóór de eerste `$`, `"` of backslash; null als [arg] geen tekst is. */
        private fun letterlijkBegin(arg: String): String? =
            if (arg.startsWith("\"")) arg.drop(1).takeWhile { it != '"' && it != '$' && it != '\\' } else null

        /**
         * De argumenten op het hoogste niveau van een aanroep, vanaf de tekst direct na "(". Houdt rekening met haakjes,
         * accolades, teksten (ook `${…}` in een tekst) en commentaar met `//`.
         */
        private fun argumenten(bron: String, start: Int): List<String> {
            val args = mutableListOf<String>()
            val stapel = ArrayDeque<Char>() // '(' '[' '{' = code, '"' = tekst, 'T' = `${` in een tekst
            var begin = start
            var i = start
            while (i < bron.length) {
                val c = bron[i]
                if (stapel.lastOrNull() == '"') {
                    when {
                        c == '\\' -> i++
                        c == '"' -> stapel.removeLast()
                        c == '$' && bron.getOrNull(i + 1) == '{' -> { stapel.addLast('T'); i++ }
                    }
                } else when {
                    c == '/' && bron.getOrNull(i + 1) == '/' -> while (i + 1 < bron.length && bron[i + 1] != '\n') i++
                    c == '"' -> stapel.addLast('"')
                    c == '(' || c == '[' || c == '{' -> stapel.addLast(c)
                    c == ')' || c == ']' || c == '}' -> if (stapel.isEmpty()) {
                        args += bron.substring(begin, i)
                        return args.map { it.trim() }.filter { it.isNotEmpty() }
                    } else stapel.removeLast()
                    c == ',' && stapel.isEmpty() -> { args += bron.substring(begin, i); begin = i + 1 }
                }
                i++
            }
            return args.map { it.trim() }.filter { it.isNotEmpty() }
        }
    }
}
