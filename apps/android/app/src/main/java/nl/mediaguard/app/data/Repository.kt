package nl.mediaguard.app.data

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import retrofit2.HttpException

/** Alles wat de schermen nodig hebben; de echte versie praat met MediaGuard, tests gebruiken een nep. */
interface Repository {
    suspend fun me(): Me
    suspend fun status(): AppStatus
    suspend fun summary(date: String?): DailySummary
    /** Laatste 24 uur (rollend); oudere servers zonder /app/recent: het overzicht van gisteren. */
    suspend fun recent(): DailySummary
    /** Meldt de koppeling; MediaGuard zet het telefoonkanaal aan en geeft het terug. */
    suspend fun paired(ntfyTopic: String?, appVersion: String?): PairedResult
    /** Voert een knop uit "Wacht op jou" uit; geeft de Nederlandse uitkomst terug. */
    suspend fun runAction(action: AttentionAction): String
    /** Antwoord van de updatecontrole van de server, of null als die (nog) niet bestaat. */
    suspend fun systemUpdate(): JsonElement?
    suspend fun control(action: String, params: Map<String, JsonElement> = emptyMap(), confirm: Boolean = false): ControlResult
    suspend fun seerrSearch(q: String): SeerrPage
    suspend fun seerrTrending(): SeerrPage
    suspend fun seerrDetail(type: String, id: Long): SeerrDetail
    suspend fun seerrRequest(type: String, id: Long, seasons: List<Int>?): SeerrRequestResult
    suspend fun myRequests(filter: String = "all"): MyRequests
    suspend fun librarySearch(q: String): LibraryPage
    /** Bibliotheeklijst per soort: [kind] series/movie, [mediaType] tv/anime/movie; [q] optioneel. */
    suspend fun libraryList(q: String?, kind: String?, mediaType: String?, pageSize: Int = 60, state: String? = null): LibraryPage
    /** Aantal items met ontbrekende afleveringen/films (KPI "Ontbrekend"). */
    suspend fun missingCount(): Int
    suspend fun libraryFiles(id: Long): LibraryFiles
    suspend fun libraryUpgrades(id: Long): UpgradeHistory
    suspend fun librarySeasons(id: Long): SeasonMatrix
    suspend fun setSeasonMonitored(id: Long, season: Int, monitored: Boolean, search: Boolean): MonitorResult
}

class ApiRepository(private val api: MediaGuardApi) : Repository {
    override suspend fun me() = api.me()
    override suspend fun status() = api.status()
    override suspend fun summary(date: String?) = api.summary(date)
    override suspend fun recent(): DailySummary = try {
        api.recent()
    } catch (e: HttpException) {
        if (e.code() != 404) throw e
        api.summary(yesterday().toString())
    }
    override suspend fun paired(ntfyTopic: String?, appVersion: String?) = api.paired(PairedBody(ntfyTopic, appVersion))
    override suspend fun runAction(action: AttentionAction): String {
        val path = ActionPaths.safe(action.path) ?: throw IllegalArgumentException(tr("Deze actie kan alleen in de web.", "This action is only available in the web UI."))
        val body = api.action(path, action.body).use { it.string() }
        return ActionPaths.message(body) ?: tr("${action.label}: gedaan", "${action.label}: done")
    }
    override suspend fun systemUpdate(): JsonElement? = try {
        api.systemUpdate().use { MgJson.parseToJsonElement(it.string()) }
    } catch (e: HttpException) {
        if (e.code() == 404 || e.code() == 405) null else throw e
    }
    override suspend fun control(action: String, params: Map<String, JsonElement>, confirm: Boolean) =
        api.control(ControlRequest(action, params, confirm))
    override suspend fun seerrSearch(q: String) = api.seerrSearch(q)
    override suspend fun seerrTrending() = api.seerrTrending()
    override suspend fun seerrDetail(type: String, id: Long) = api.seerrDetail(type, id)
    override suspend fun seerrRequest(type: String, id: Long, seasons: List<Int>?): SeerrRequestResult {
        val s: JsonElement? = when {
            type != "tv" -> null
            seasons == null -> JsonPrimitive("all")
            else -> JsonArray(seasons.map { JsonPrimitive(it) })
        }
        return api.seerrRequest(SeerrRequestBody(type, id, s))
    }
    override suspend fun myRequests(filter: String) = api.myRequests(filter)
    override suspend fun librarySearch(q: String) = api.library(q = q)
    override suspend fun libraryList(q: String?, kind: String?, mediaType: String?, pageSize: Int, state: String?) =
        api.library(q = q?.ifBlank { null }, kind = kind, mediaType = mediaType, state = state, pageSize = pageSize)
    override suspend fun missingCount() = api.library(state = "missing", pageSize = 1).total
    override suspend fun libraryFiles(id: Long) = api.libraryFiles(id)
    override suspend fun libraryUpgrades(id: Long) = api.libraryUpgrades(id)
    override suspend fun librarySeasons(id: Long) = api.librarySeasons(id)
    override suspend fun setSeasonMonitored(id: Long, season: Int, monitored: Boolean, search: Boolean) =
        api.setSeasonMonitored(id, season, MonitorBody(monitored, search))
}

/** Paden van knoppen uit "Wacht op jou" (komen van de server, toch streng controleren). */
object ActionPaths {
    private val ALLOWED = Regex("^/[A-Za-z0-9._~\\-]+(/[A-Za-z0-9._~\\-]+)*$")

    /** "/encoding/jobs/12/activate" → "encoding/jobs/12/activate"; null bij iets vreemds (.., //, query, schema). */
    fun safe(path: String): String? {
        if (!ALLOWED.matches(path) || path.split('/').any { it == ".." || it == "." }) return null
        return path.removePrefix("/")
    }

    /** Uitkomst uit het antwoord: message_nl, message of detail; anders null. */
    fun message(body: String): String? = runCatching {
        val o = MgJson.parseToJsonElement(body).jsonObject
        listOf("message_nl", "message", "detail").firstNotNullOfOrNull { k ->
            (o[k] as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() }
        }
    }.getOrNull()
}
