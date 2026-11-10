package nl.mediaguard.app.shots

import nl.mediaguard.app.data.AppStatus
import nl.mediaguard.app.data.Attention
import nl.mediaguard.app.data.AttentionAction
import nl.mediaguard.app.data.AttentionItem
import nl.mediaguard.app.data.AttentionRowItem
import nl.mediaguard.app.data.EmergencyStop
import nl.mediaguard.app.data.CustomFormatScore
import nl.mediaguard.app.data.DailySummary
import nl.mediaguard.app.data.Delivery
import nl.mediaguard.app.data.DeliveryProgress
import nl.mediaguard.app.data.DownloadItem
import nl.mediaguard.app.data.DownloadedFile
import nl.mediaguard.app.data.Downloads
import nl.mediaguard.app.data.EncoderStatus
import nl.mediaguard.app.data.Lane
import nl.mediaguard.app.data.LibraryFile
import nl.mediaguard.app.data.LibraryFiles
import nl.mediaguard.app.data.LibraryItem
import nl.mediaguard.app.data.LibraryPage
import nl.mediaguard.app.data.LibraryStats
import nl.mediaguard.app.data.MediaInfo
import nl.mediaguard.app.data.MyRequest
import nl.mediaguard.app.data.MyRequests
import nl.mediaguard.app.data.ProfileInfo
import nl.mediaguard.app.data.QualityInfo
import nl.mediaguard.app.data.ReplacedFile
import nl.mediaguard.app.data.RequestEntry
import nl.mediaguard.app.data.RunningEncode
import nl.mediaguard.app.data.SeasonEpisode
import nl.mediaguard.app.data.SeasonInfo
import nl.mediaguard.app.data.SeasonMatrix
import nl.mediaguard.app.data.SeerrDetail
import nl.mediaguard.app.data.SeerrPage
import nl.mediaguard.app.data.SeerrResult
import nl.mediaguard.app.data.SeerrSeason
import nl.mediaguard.app.data.StorageStatus
import nl.mediaguard.app.data.SummaryAttention
import nl.mediaguard.app.data.SummaryEncoder
import nl.mediaguard.app.data.SummaryStorage
import nl.mediaguard.app.data.UpgradeEntry
import nl.mediaguard.app.data.UpgradeHistory
import nl.mediaguard.app.data.UpgradeHistoryEntry
import nl.mediaguard.app.data.UpgradeSide
import nl.mediaguard.app.ui.ItemDetail

/** Voorbeeldgegevens voor de screenshots (verzonnen titels in de vorm van echte API-antwoorden). */
object Sample {
    private const val GB = 1024L * 1024 * 1024

    val status = AppStatus(
        version = "1.2.1",
        attention = Attention(count = 3, errors = 1, items = listOf(
            AttentionItem("encoding.conflict", "1 vervanging met een conflict", "error", "/quarantaine",
                detail = "MediaGuard kon niet met zekerheid vaststellen of het origineel of het AV1-bestand actief is. " +
                    "Er is niets verwijderd; kies per bestand.",
                rows = listOf(AttentionRowItem("108", "Noorderlicht S02E04.mkv", "Beide bestanden gevonden",
                    actions = listOf(AttentionAction("Origineel houden", path = "/encoding/jobs/108/cancel",
                        confirm = "Het AV1-resultaat wordt weggegooid."))))),
            AttentionItem("encoder.paused.gpu", "AV1 GPU-baan staat stil", "warning", "/av1",
                detail = "cache heeft nog 18 GB vrij, onder de pauzebodem van 20 GB; encoderen gepauzeerd tot er weer ruimte is"),
            AttentionItem("library.locked", "Bibliotheek nog vergrendeld", "warning", "/",
                detail = "Er loopt geen migratie meer, maar MediaGuard mag nog geen bestanden vervangen.", adminActions = 1),
        )),
        encoder = EncoderStatus(
            reachable = true,
            lanes = listOf(Lane("gpu", 2, 4), Lane("cpu", 1, 1, reason = null)),
            running = listOf(
                RunningEncode(115, "Kustwacht S01E03", "gpu", 62.5, 540, "Encoderen"),
                RunningEncode(116, "De Lange Winter (2019)", "gpu", 18.0, 2400, "Encoderen"),
                RunningEncode(117, "Stadslicht S03E01", "cpu", 41.2, 3900, "Encoderen · Dolby Vision"),
            ),
            queued = 8,
        ),
        downloads = Downloads(active = 2, clientsOk = true, items = listOf(
            DownloadItem("Bergpas S01E07", "SABnzbd", 74.0, 300, 2 * GB),
            DownloadItem("Het Eiland (2024)", "SABnzbd", 21.0, 1500, 9 * GB),
        )),
        storage = StorageStatus(freeBytes = 3_400 * GB, daysLeft = "± 300 dagen"),
        savingsBytes = 834 * GB,
    )

    val calmStatus = status.copy(attention = Attention())

    val stopStatus = calmStatus.copy(emergencyStop = EmergencyStop(true, "Schijf vervangen", "2026-11-08T09:12:00Z"),
        encoder = status.encoder.copy(running = status.encoder.running.map { it.copy(phase = "Gepauzeerd (noodstop)", etaSeconds = null) }))

    val summary = DailySummary(
        date = "2026-11-07",
        headline = "Gisteren: 14 afleveringen en 2 films erbij; AV1 bespaarde 41 GB",
        replaced = listOf(
            ReplacedFile("Kustwacht", "S01E02", 8 * GB, 3 * GB, 5 * GB, 95.8, "2026-11-07T21:14:00Z"),
            ReplacedFile("De Lange Winter", null, 22 * GB, 9 * GB, 13 * GB, 96.4, "2026-11-07T17:02:00Z"),
            ReplacedFile("Stadslicht", "S02E10", 6 * GB, 2 * GB, 4 * GB, 97.1, "2026-11-07T09:40:00Z"),
        ),
        downloaded = listOf(
            DownloadedFile("Bergpas", "S01E06", "nieuw", 2 * GB, "WEBDL-1080p", "IndexerNL", "2026-11-07T20:01:00Z"),
            DownloadedFile("Het Archief", null, "upgrade", 14 * GB, "Bluray-2160p", "NZBgeek", "2026-11-07T12:33:00Z"),
        ),
        upgrades = listOf(UpgradeEntry("Het Archief", null, "Betere bron: Bluray in plaats van WEB", "2026-11-07T12:33:00Z")),
        requests = listOf(RequestEntry("Noorderlicht", "tv", "2026-11-07T10:00:00Z", "Wordt gezocht")),
        attention = listOf(SummaryAttention("1 encode mislukt (Noorderlicht S02E04)", "error", "/av1")),
        storage = SummaryStorage(netBytes = 19 * GB, addedBytes = 60 * GB, savedBytes = 41 * GB, unexplainedBytes = 3 * GB),
        encoder = SummaryEncoder(done = 12, failed = 1, savedBytes = 41 * GB),
    )

    val recent = summary.copy(date = "", window = "24h", label = "Laatste 24 uur",
        headline = "Laatste 24 uur: 9 afleveringen, 1 film erbij; 3 omgezet naar AV1 (22,0 GB bespaard)")

    val library = LibraryPage(total = 412, items = listOf(
        LibraryItem(1, "Bergpas", 2025, "series", "Sonarr", "tv", null, 48 * GB, true, "Doorlopend", LibraryStats(16, 14, 2, 0, 0, 0)),
        LibraryItem(2, "Het Eiland", 2024, "series", "Sonarr", "tv", null, 31 * GB, true, "Afgelopen", LibraryStats(10, 10, 0, 0, 0, 3)),
        LibraryItem(3, "Kustwacht", 2023, "series", "Sonarr", "tv", null, 22 * GB, true, "Doorlopend", LibraryStats(24, 24, 0, 2, 0, 0)),
        LibraryItem(4, "Noorderlicht", 2021, "series", "Sonarr", "tv", null, 64 * GB, true, "Afgelopen", LibraryStats(30, 30, 0, 0, 0, 0)),
        LibraryItem(5, "Stadslicht", 2019, "series", "Sonarr", "tv", null, 12 * GB, false, "Afgelopen", LibraryStats(12, 6, 0, 0, 6, 0)),
        LibraryItem(6, "Zomerhuis", 2022, "series", "Sonarr", "tv", null, 9 * GB, true, "Afgelopen", LibraryStats(8, 8, 0, 0, 0, 0)),
    ))

    val detail = ItemDetail(
        files = LibraryFiles(1, "series", ProfileInfo("MediaGuard · 1080p Dual Audio", "WEBDL-1080p", 2200), listOf(
            LibraryFile(11, "S01E01", 1, 1_500_000_000, QualityInfo("WEBDL-1080p", 1080, "web"),
                MediaInfo("AV1", "HDR10", listOf("nld", "eng"), listOf("nld")), listOf(CustomFormatScore("MediaGuard · Dual Audio", 2000)),
                2200, true, true, "WEBDL-1080p · AV1 · HDR10 · 1,4 GB"),
            LibraryFile(12, "S01E02", 1, 1_900_000_000, QualityInfo("WEBDL-1080p", 1080, "web"),
                MediaInfo("HEVC", "SDR", listOf("eng"), listOf("nld")), emptyList(), 1500, false, false, "WEBDL-1080p · HEVC · 1,8 GB"),
            LibraryFile(13, "S01E03", 1, 1_400_000_000, QualityInfo("WEBDL-1080p", 1080, "web"),
                MediaInfo("AV1", "SDR", listOf("nld", "eng"), listOf("nld")), emptyList(), 2200, true, true, "WEBDL-1080p · AV1 · 1,3 GB"),
        )),
        upgrades = UpgradeHistory(1, listOf(
            UpgradeHistoryEntry("2026-11-05T14:00:00Z", "S01E01", UpgradeSide("WEBDL-720p", 900), UpgradeSide("WEBDL-1080p", 2200),
                "IndexerNL", "Hogere resolutie en Nederlandse audio"),
            UpgradeHistoryEntry("2026-10-28T09:12:00Z", "S01E03", UpgradeSide("HDTV-1080p", 400, av1ByMediaGuard = true),
                UpgradeSide("WEBDL-1080p", 2200), "NZBgeek", "Betere bron: WEB in plaats van HDTV"),
        )),
        seasons = SeasonMatrix("Bergpas", listOf(
            SeasonInfo(1, true, (1..8).map { SeasonEpisode(it.toLong(), it, null, true, true) }),
            SeasonInfo(2, true, (1..8).map { SeasonEpisode(10L + it, it, null, true, it <= 6) }),
            SeasonInfo(3, false, (1..6).map { SeasonEpisode(20L + it, it, null, false, false) }),
        )),
    )

    val seerr = SeerrPage(results = listOf(
        SeerrResult("tv", 1, "Bergpas", 2025, null, null, "gedeeltelijk", "Gedeeltelijk"),
        SeerrResult("movie", 2, "Het Eiland", 2024, null, null, "beschikbaar", "Beschikbaar"),
        SeerrResult("tv", 3, "Noorderlicht", 2021, null, null, "aangevraagd", "Aangevraagd"),
        SeerrResult("movie", 4, "Zomerhuis", 2022, null, null, "niet", null),
    ))

    val mine = MyRequests(listOf(
        MyRequest(77, "tv", 3, "Noorderlicht", 2021, null, "2026-11-07T10:00:00Z", "jan", "Goedgekeurd",
            Delivery("active", "Wordt gezocht", DeliveryProgress(18, 30), "MediaGuard zoekt de ontbrekende afleveringen")),
        MyRequest(76, "movie", 2, "Het Eiland", 2024, null, "2026-11-02T18:30:00Z", "jan", "Beschikbaar",
            Delivery("delivered", "Geleverd", DeliveryProgress(1, 1), null)),
    ))

    val seerrDetail = SeerrDetail("tv", 1, "Bergpas", 2025, null, null,
        "Een berggids raakt verstrikt in een zaak die het dorp al jaren verzwijgt.", "gedeeltelijk", "Gedeeltelijk",
        listOf("Drama", "Thriller"), 48, 7.9, listOf(
            SeerrSeason(1, 8, "beschikbaar", "Beschikbaar"),
            SeerrSeason(2, 8, "gedeeltelijk", "6 van 8"),
            SeerrSeason(3, 6, "niet", "Niet aangevraagd"),
        ))
}
