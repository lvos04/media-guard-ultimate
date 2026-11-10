package nl.mediaguard.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

// Modellen volgen docs/APP_API.md. Alle velden hebben een standaardwaarde zodat een oudere of
// nieuwere MediaGuard-versie de app niet laat crashen (onbekende velden worden genegeerd).

@Serializable
data class Me(
    val username: String = "",
    val role: String = "",
)

// -- /app/status ----------------------------------------------------------------------------
@Serializable
data class EmergencyStop(val active: Boolean = false, val reason: String? = null, val since: String? = null)

/** Knop uit "Wacht op jou": hetzelfde endpoint als de web (POST /api/v1 + path). */
@Serializable
data class AttentionAction(
    @SerialName("label_nl") val label: String = "",
    val method: String = "post",
    val path: String = "",
    val body: JsonObject = JsonObject(emptyMap()),
    @SerialName("confirm_nl") val confirm: String? = null,
    val primary: Boolean = false,
) {
    /** Op de telefoon altijd eerst bevestigen (een tik is snel gemist), behalve knoppen die alleen iets
     *  opnieuw proberen, controleren of een melding verbergen. Zo veranderen bestanden ("Activeren", "Verplaatsen",
     *  "Origineel terugzetten") alleen na een bewuste keuze. */
    val needsConfirm: Boolean
        get() = !confirm.isNullOrBlank() || (body["confirm"] as? JsonPrimitive)?.booleanOrNull == true ||
            NO_CONFIRM.none { it.matches(path) }

    companion object {
        private val NO_CONFIRM = listOf(
            Regex("^/encoding/autopilot/run$"),
            Regex("^/storage/probe$"),
            Regex("^/quarantine/\\d+/release-check$"),
            Regex("^/system/update/dismiss$"),
        )
    }
}

@Serializable
data class AttentionRowItem(
    val id: String = "",
    val title: String = "",
    val detail: String? = null,
    val href: String? = null,
    val actions: List<AttentionAction> = emptyList(),
)

@Serializable
data class AttentionItem(
    val key: String = "",
    @SerialName("title_nl") val title: String = "",
    val severity: String = "info",
    val href: String? = null,
    @SerialName("detail_nl") val detail: String? = null,
    val count: Int = 1,
    val actions: List<AttentionAction> = emptyList(),
    val rows: List<AttentionRowItem> = emptyList(),
    @SerialName("more_rows") val moreRows: Int = 0,
    /** Knoppen die alleen een beheerder mag (die staan alleen in de web). */
    @SerialName("admin_actions") val adminActions: Int = 0,
)

@Serializable
data class Attention(val count: Int = 0, val errors: Int = 0, val items: List<AttentionItem> = emptyList())

@Serializable
data class Lane(
    val lane: String = "",
    val running: Int = 0,
    val slots: Int = 0,
    val paused: Boolean = false,
    @SerialName("reason_nl") val reason: String? = null,
)

@Serializable
data class RunningEncode(
    /** Core-job; null als alleen de AV1-engine hem kent (dan geen "opnieuw proberen"). */
    @SerialName("job_id") val jobId: Long? = null,
    val title: String = "",
    val lane: String = "",
    val percent: Double? = null,
    @SerialName("eta_seconds") val etaSeconds: Long? = null,
    @SerialName("phase_nl") val phase: String? = null,
)

@Serializable
data class EncoderStatus(
    val reachable: Boolean = false,
    val lanes: List<Lane> = emptyList(),
    val running: List<RunningEncode> = emptyList(),
    val queued: Int = 0,
)

@Serializable
data class DownloadItem(
    val title: String = "",
    val client: String = "",
    val percent: Double? = null,
    @SerialName("eta_seconds") val etaSeconds: Long? = null,
    val size: Long? = null,
)

@Serializable
data class Downloads(
    val active: Int = 0,
    /** null = onbekend (downloadstatus kon niet worden opgehaald). */
    @SerialName("clients_ok") val clientsOk: Boolean? = null,
    val items: List<DownloadItem> = emptyList(),
)

@Serializable
data class StorageStatus(
    @SerialName("free_bytes") val freeBytes: Long? = null,
    @SerialName("days_left_nl") val daysLeft: String? = null,
)

@Serializable
data class AppStatus(
    val version: String = "",
    @SerialName("generated_at") val generatedAt: String? = null,
    @SerialName("emergency_stop") val emergencyStop: EmergencyStop = EmergencyStop(),
    val attention: Attention = Attention(),
    val encoder: EncoderStatus = EncoderStatus(),
    val downloads: Downloads = Downloads(),
    val storage: StorageStatus = StorageStatus(),
    @SerialName("savings_bytes") val savingsBytes: Long = 0,
)

// -- /app/summary ---------------------------------------------------------------------------
@Serializable
data class ReplacedFile(
    val title: String = "",
    val label: String? = null,
    @SerialName("from_size") val fromSize: Long? = null,
    @SerialName("to_size") val toSize: Long? = null,
    val saved: Long? = null,
    val vmaf: Double? = null,
    val at: String? = null,
)

@Serializable
data class DownloadedFile(
    val title: String = "",
    val label: String? = null,
    val kind: String = "nieuw",
    val size: Long? = null,
    val quality: String? = null,
    @SerialName("source_nl") val source: String? = null,
    val at: String? = null,
)

@Serializable
data class UpgradeEntry(
    val title: String = "",
    val label: String? = null,
    @SerialName("reason_nl") val reason: String? = null,
    val at: String? = null,
)

@Serializable
data class RequestEntry(
    val title: String = "",
    @SerialName("media_type") val mediaType: String = "",
    @SerialName("requested_at") val requestedAt: String? = null,
    @SerialName("delivery_state_nl") val deliveryState: String? = null,
)

@Serializable
data class SummaryAttention(
    @SerialName("title_nl") val title: String = "",
    val severity: String = "info",
    val href: String? = null,
)

@Serializable
data class SummaryStorage(
    @SerialName("net_bytes") val netBytes: Long = 0,
    @SerialName("added_bytes") val addedBytes: Long = 0,
    @SerialName("saved_bytes") val savedBytes: Long = 0,
    /** Niet aan MediaGuard of Sonarr/Radarr toe te wijzen; null = (nog) niet gemeten. */
    @SerialName("unexplained_bytes") val unexplainedBytes: Long? = null,
)

@Serializable
data class SummaryEncoder(
    val done: Int = 0,
    val failed: Int = 0,
    @SerialName("saved_bytes") val savedBytes: Long = 0,
)

@Serializable
data class DailySummary(
    val date: String = "",
    @SerialName("headline_nl") val headline: String = "",
    val replaced: List<ReplacedFile> = emptyList(),
    val downloaded: List<DownloadedFile> = emptyList(),
    val upgrades: List<UpgradeEntry> = emptyList(),
    val requests: List<RequestEntry> = emptyList(),
    val attention: List<SummaryAttention> = emptyList(),
    val storage: SummaryStorage = SummaryStorage(),
    val encoder: SummaryEncoder = SummaryEncoder(),
    /** "Laatste 24 uur", "Gisteren", … (oudere servers: leeg). */
    @SerialName("label_nl") val label: String? = null,
    /** "24h" bij het rollende venster van /app/recent; null bij een dagoverzicht. */
    val window: String? = null,
)

// -- /app/control ---------------------------------------------------------------------------
@Serializable
data class ControlRequest(
    val action: String,
    val params: Map<String, JsonElement> = emptyMap(),
    val confirm: Boolean = false,
)

@Serializable
data class ControlResult(
    val ok: Boolean = false,
    @SerialName("message_nl") val message: String = "",
    val ntfy: NtfyInfo? = null,
)

@Serializable
data class PairedBody(
    @SerialName("ntfy_topic") val ntfyTopic: String? = null,
    @SerialName("app_version") val appVersion: String? = null,
)

@Serializable
data class PairedResult(
    val ok: Boolean = false,
    val ntfy: NtfyInfo? = null,
    @SerialName("message_nl") val message: String = "",
)

// -- Seerr ----------------------------------------------------------------------------------
@Serializable
data class SeerrResult(
    @SerialName("media_type") val mediaType: String = "movie",
    @SerialName("tmdb_id") val tmdbId: Long = 0,
    val title: String = "",
    val year: Int? = null,
    @SerialName("poster_url") val posterUrl: String? = null,
    val overview: String? = null,
    val status: String? = null,
    @SerialName("status_nl") val statusNl: String? = null,
)

@Serializable
data class SeerrPage(
    val page: Int = 1,
    @SerialName("total_pages") val totalPages: Int = 1,
    val results: List<SeerrResult> = emptyList(),
)

@Serializable
data class SeerrSeason(
    @SerialName("season_number") val seasonNumber: Int = 0,
    val episodes: Int? = null,
    val status: String? = null,
    @SerialName("status_nl") val statusNl: String? = null,
)

@Serializable
data class SeerrDetail(
    @SerialName("media_type") val mediaType: String = "movie",
    @SerialName("tmdb_id") val tmdbId: Long = 0,
    val title: String = "",
    val year: Int? = null,
    @SerialName("poster_url") val posterUrl: String? = null,
    @SerialName("backdrop_url") val backdropUrl: String? = null,
    val overview: String? = null,
    val status: String? = null,
    @SerialName("status_nl") val statusNl: String? = null,
    val genres: List<String> = emptyList(),
    val runtime: Int? = null,
    val rating: Double? = null,
    val seasons: List<SeerrSeason> = emptyList(),
)

@Serializable
data class SeerrRequestBody(
    @SerialName("media_type") val mediaType: String,
    @SerialName("tmdb_id") val tmdbId: Long,
    val seasons: JsonElement? = null,
    val is4k: Boolean = false,
)

@Serializable
data class SeerrRequestResult(
    @SerialName("request_id") val requestId: Long? = null,
    val status: String? = null,
    @SerialName("message_nl") val message: String = "",
)

@Serializable
data class DeliveryProgress(val present: Int = 0, val total: Int = 0)

@Serializable
data class Delivery(
    val state: String? = null,
    @SerialName("state_nl") val stateNl: String? = null,
    val progress: DeliveryProgress? = null,
    @SerialName("next_step_nl") val nextStep: String? = null,
)

@Serializable
data class MyRequest(
    @SerialName("request_id") val requestId: Long = 0,
    @SerialName("media_type") val mediaType: String = "",
    @SerialName("tmdb_id") val tmdbId: Long = 0,
    val title: String = "",
    val year: Int? = null,
    @SerialName("poster_url") val posterUrl: String? = null,
    @SerialName("requested_at") val requestedAt: String? = null,
    @SerialName("requested_by") val requestedBy: String? = null,
    @SerialName("seerr_status_nl") val seerrStatus: String? = null,
    val delivery: Delivery? = null,
)

@Serializable
data class MyRequests(val results: List<MyRequest> = emptyList())

// -- Bibliotheek ----------------------------------------------------------------------------
@Serializable
data class LibraryItem(
    val id: Long = 0,
    val title: String = "",
    val year: Int? = null,
    val kind: String = "",
    @SerialName("instance_name") val instanceName: String? = null,
    @SerialName("media_type") val mediaType: String? = null,
    @SerialName("poster_url") val posterUrl: String? = null,
    @SerialName("size_on_disk") val sizeOnDisk: Long = 0,
    val monitored: Boolean? = null,
    @SerialName("status_nl") val statusNl: String? = null,
    val stats: LibraryStats? = null,
)

/** Telling per item zoals de web-bibliotheek die toont (film: total = 1). */
@Serializable
data class LibraryStats(
    val total: Int = 0,
    val available: Int = 0,
    val missing: Int = 0,
    val upcoming: Int = 0,
    val unmonitored: Int = 0,
    @SerialName("cutoff_unmet") val cutoffUnmet: Int = 0,
)

@Serializable
data class LibraryPage(val total: Int = 0, val items: List<LibraryItem> = emptyList())

@Serializable
data class QualityInfo(val name: String? = null, val resolution: Int? = null, val source: String? = null)

@Serializable
data class MediaInfo(
    @SerialName("video_codec") val videoCodec: String? = null,
    @SerialName("dynamic_range") val dynamicRange: String? = null,
    @SerialName("audio_languages") val audioLanguages: List<String> = emptyList(),
    @SerialName("subtitle_languages") val subtitleLanguages: List<String> = emptyList(),
)

@Serializable
data class CustomFormatScore(val name: String = "", val score: Int = 0)

@Serializable
data class LibraryFile(
    @SerialName("file_id") val fileId: Long = 0,
    val label: String? = null,
    @SerialName("season_number") val seasonNumber: Int? = null,
    val size: Long? = null,
    val quality: QualityInfo = QualityInfo(),
    val media: MediaInfo = MediaInfo(),
    @SerialName("custom_formats") val customFormats: List<CustomFormatScore> = emptyList(),
    val score: Int? = null,
    @SerialName("cutoff_met") val cutoffMet: Boolean? = null,
    @SerialName("av1_by_mediaguard") val av1ByMediaGuard: Boolean = false,
    @SerialName("summary_nl") val summary: String? = null,
)

@Serializable
data class ProfileInfo(
    val name: String? = null,
    val cutoff: String? = null,
    @SerialName("cutoff_score") val cutoffScore: Int? = null,
)

@Serializable
data class LibraryFiles(
    @SerialName("item_id") val itemId: Long = 0,
    val kind: String = "",
    val profile: ProfileInfo = ProfileInfo(),
    val files: List<LibraryFile> = emptyList(),
)

@Serializable
data class UpgradeSide(
    val quality: String? = null,
    val score: Int? = null,
    val size: Long? = null,
    @SerialName("release_group") val releaseGroup: String? = null,
    @SerialName("av1_by_mediaguard") val av1ByMediaGuard: Boolean = false,
)

@Serializable
data class UpgradeHistoryEntry(
    val at: String? = null,
    val label: String? = null,
    val old: UpgradeSide = UpgradeSide(),
    val new: UpgradeSide = UpgradeSide(),
    @SerialName("source_nl") val source: String? = null,
    @SerialName("reason_nl") val reason: String = "",
)

@Serializable
data class UpgradeHistory(
    @SerialName("item_id") val itemId: Long = 0,
    val upgrades: List<UpgradeHistoryEntry> = emptyList(),
)

@Serializable
data class SeasonEpisode(
    @SerialName("episode_id") val episodeId: Long = 0,
    @SerialName("episode_number") val episodeNumber: Int = 0,
    val title: String? = null,
    val monitored: Boolean = false,
    @SerialName("has_file") val hasFile: Boolean = false,
)

@Serializable
data class SeasonInfo(
    @SerialName("season_number") val seasonNumber: Int = 0,
    val monitored: Boolean = false,
    val episodes: List<SeasonEpisode> = emptyList(),
)

@Serializable
data class SeasonMatrix(val title: String? = null, val seasons: List<SeasonInfo> = emptyList())

@Serializable
data class MonitorBody(val monitored: Boolean, val search: Boolean = false)

@Serializable
data class MonitorResult(@SerialName("message_nl") val message: String = "")
