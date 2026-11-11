package nl.mediaguard.app.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import nl.mediaguard.app.data.AppStatus
import nl.mediaguard.app.data.AttentionAction
import nl.mediaguard.app.data.Freshness
import nl.mediaguard.app.data.UpdateInfo
import nl.mediaguard.app.data.DailySummary
import nl.mediaguard.app.data.LibraryFiles
import nl.mediaguard.app.data.LibraryPage
import nl.mediaguard.app.data.Me
import nl.mediaguard.app.data.MyRequests
import nl.mediaguard.app.data.Repository
import nl.mediaguard.app.data.SeasonMatrix
import nl.mediaguard.app.data.SeerrDetail
import nl.mediaguard.app.data.SeerrPage
import nl.mediaguard.app.data.UpgradeHistory
import nl.mediaguard.app.data.friendlyMessage
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.data.yesterday
import java.time.LocalDate

/** Basis: laadt één waarde, houdt de vorige zichtbaar tijdens verversen. */
abstract class LoadViewModel<T> : ViewModel() {
    protected val _state = MutableStateFlow<UiState<T>>(UiState.Loading)
    val state: StateFlow<UiState<T>> = _state.asStateFlow()

    protected fun load(onDone: (Throwable?) -> Unit = {}, block: suspend () -> T) {
        val previous = (_state.value as? UiState.Data)?.value
        _state.value = if (previous != null) UiState.Data(previous, refreshing = true) else UiState.Loading
        viewModelScope.launch {
            val result = runCatching { block() }
            _state.value = result.fold(
                { UiState.Data(it) },
                { e -> if (previous != null) UiState.Data(previous) else UiState.Error(e.friendlyMessage()) },
            )
            onDone(result.exceptionOrNull())
        }
    }
}

class TodayViewModel(private val repo: Repository) : LoadViewModel<DailySummary>() {
    private val _date = MutableStateFlow(yesterday())
    val date: StateFlow<LocalDate> = _date.asStateFlow()

    init { refresh() }

    fun setDate(d: LocalDate) { _date.value = d; _state.value = UiState.Loading; refresh() }
    fun refresh() = load { repo.summary(_date.value.toString()) }
}

/** Geschiedenis: de dagoverzichten van de afgelopen dagen (nieuwste eerst). */
class HistoryViewModel(private val repo: Repository, private val days: Int = 7) : LoadViewModel<List<DailySummary>>() {
    fun refresh() = load {
        coroutineScope {
            (0 until days).map { i -> yesterday().minusDays(i.toLong()) }
                .map { d -> async { runCatching { repo.summary(d.toString()) }.getOrElse { DailySummary(date = d.toString()) } } }
                .awaitAll()
        }
    }
    fun ensureLoaded() { if (state.value !is UiState.Data) refresh() }
}

/** Laatste 24 uur (rollend) voor Overzicht en Ruimte; oudere servers: gisteren. */
class RecentViewModel(private val repo: Repository) : LoadViewModel<DailySummary>() {
    init { refresh() }
    fun refresh() = load { repo.recent() }
}

class StatusViewModel(
    private val repo: Repository,
    private val appVersion: String = "",
    private val clock: () -> Long = System::currentTimeMillis,
) : LoadViewModel<AppStatus>() {
    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()
    private val _me = MutableStateFlow<Me?>(null)
    /** Ingelogde gebruiker (voor de initialen rechtsboven). */
    val me: StateFlow<Me?> = _me.asStateFlow()
    private val _missing = MutableStateFlow<Int?>(null)
    /** Aantal ontbrekende items; null = (nog) onbekend of niet beschikbaar op deze core. */
    val missing: StateFlow<Int?> = _missing.asStateFlow()
    private val _freshness = MutableStateFlow(Freshness())
    /** Wanneer de status voor het laatst lukte en of de laatste poging faalde ("Niet bijgewerkt sinds …"). */
    val freshness: StateFlow<Freshness> = _freshness.asStateFlow()
    private val _update = MutableStateFlow<UpdateInfo?>(null)
    /** Nieuwere server- of app-versie (uit de status en de updatecontrole van de server). */
    val update: StateFlow<UpdateInfo?> = _update.asStateFlow()
    private var updateJson: JsonElement? = null
    private var updateAt = 0L
    private var poller: Job? = null
    private var extrasAt = 0L

    fun refresh() {
        load(onDone = { e ->
            _freshness.value = if (e == null) Freshness(clock(), null)
            else _freshness.value.copy(error = e.friendlyMessage())
            if (e == null) recomputeUpdate()
        }) { repo.status() }
        // Zwaardere gegevens niet elke 20 s: hooguit eens per 5 minuten (of bij handmatig verversen).
        if (clock() - extrasAt > 5 * 60_000) refreshExtras()
    }

    fun refreshExtras() {
        extrasAt = clock()
        viewModelScope.launch {
            if (_me.value == null) runCatching { repo.me() }.onSuccess { _me.value = it }
            runCatching { repo.missingCount() }.onSuccess { _missing.value = it }
            if (clock() - updateAt > 60 * 60_000) {
                updateAt = clock()
                runCatching { repo.systemUpdate() }.onSuccess { updateJson = it }
                recomputeUpdate()
            }
        }
    }

    private fun recomputeUpdate() {
        val server = (state.value as? UiState.Data)?.value?.version?.ifBlank { null }
        if (server == null && updateJson == null) return
        _update.value = UpdateInfo.from(updateJson, server, appVersion)
    }

    /** Ververst elke 20 s zolang het scherm zichtbaar is. */
    fun startPolling() {
        if (poller?.isActive == true) return
        poller = viewModelScope.launch { while (isActive) { refresh(); delay(20_000) } }
    }
    fun stopPolling() { poller?.cancel() }

    fun control(action: String, params: Map<String, JsonElement> = emptyMap(), confirm: Boolean = false) {
        viewModelScope.launch {
            _message.value = runCatching { repo.control(action, params, confirm).message }
                .getOrElse { it.friendlyMessage() }
            refresh()
        }
    }
    fun emergency(on: Boolean, reason: String) = control(
        if (on) "emergency_stop_on" else "emergency_stop_off",
        mapOf("reason" to JsonPrimitive(reason.ifBlank { tr("Via de Android-app", "Via the Android app") })), confirm = true)
    fun autopilot() = control("run_autopilot")
    fun lane(lane: String, pause: Boolean) =
        control(if (pause) "pause_lane" else "resume_lane", mapOf("lane" to JsonPrimitive(lane)), confirm = true)
    fun retryJob(id: Long) = control("retry_job", mapOf("job_id" to JsonPrimitive(id)))

    /** Knop uit "Wacht op jou" (de bevestiging is al gevraagd als dat nodig is). */
    fun runAction(action: AttentionAction) {
        viewModelScope.launch {
            _message.value = runCatching { repo.runAction(action) }.getOrElse { it.friendlyMessage() }
            refresh()
        }
    }
    fun consumeMessage() { _message.value = null }
}

class SeerrSearchViewModel(private val repo: Repository) : LoadViewModel<SeerrPage>() {
    init { load { repo.seerrTrending() } }
    fun search(q: String) = if (q.isBlank()) load { repo.seerrTrending() } else load { repo.seerrSearch(q.trim()) }
}

class MyRequestsViewModel(private val repo: Repository) : LoadViewModel<MyRequests>() {
    init { refresh() }
    fun refresh() = load { repo.myRequests() }
}

class SeerrDetailViewModel(private val repo: Repository, private val type: String, private val id: Long) :
    LoadViewModel<SeerrDetail>() {
    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()
    init { refresh() }
    fun refresh() = load { repo.seerrDetail(type, id) }
    fun request(seasons: List<Int>?) {
        viewModelScope.launch {
            _message.value = runCatching { repo.seerrRequest(type, id, seasons).message.ifBlank { tr("Aangevraagd", "Requested") } }
                .getOrElse { it.friendlyMessage() }
            refresh()
        }
    }
    fun consumeMessage() { _message.value = null }
}

/** Soort in de bibliotheek, zoals de segmented control in de web: Series · Anime · Films. */
enum class LibraryKind(private val nl: String, private val en: String, val kind: String, val mediaType: String?) {
    Series("Series", "Series", "series", "tv"),
    Anime("Anime", "Anime", "series", "anime"),
    Films("Films", "Movies", "movie", null);

    val label: String get() = tr(nl, en)
}

class LibrarySearchViewModel(private val repo: Repository, missingOnly: Boolean = false) : LoadViewModel<LibraryPage>() {
    private val _kind = MutableStateFlow(LibraryKind.Series)
    val kind: StateFlow<LibraryKind> = _kind.asStateFlow()
    private val _missingOnly = MutableStateFlow(missingOnly)
    /** Alleen titels met ontbrekende afleveringen/films, over alle soorten (tegel "Ontbrekend"). */
    val missingOnly: StateFlow<Boolean> = _missingOnly.asStateFlow()
    private var query = ""

    init { refresh() }

    fun setKind(k: LibraryKind) { if (k != _kind.value) { _kind.value = k; _state.value = UiState.Loading; refresh() } }
    fun setMissingOnly(on: Boolean) { if (on != _missingOnly.value) { _missingOnly.value = on; _state.value = UiState.Loading; refresh() } }
    fun search(q: String) { query = q.trim(); _state.value = UiState.Loading; refresh() }
    fun refresh() {
        val k = _kind.value
        if (_missingOnly.value) load { repo.libraryList(query, null, null, state = "missing") }
        else load { repo.libraryList(query, k.kind, k.mediaType) }
    }
}

data class ItemDetail(val files: LibraryFiles, val upgrades: UpgradeHistory, val seasons: SeasonMatrix?)

class LibraryItemViewModel(private val repo: Repository, private val id: Long) : LoadViewModel<ItemDetail>() {
    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()
    init { refresh() }
    fun refresh() = load {
        val files = repo.libraryFiles(id)
        val upgrades = runCatching { repo.libraryUpgrades(id) }.getOrElse { UpgradeHistory(id) }
        val seasons = if (files.kind == "series") runCatching { repo.librarySeasons(id) }.getOrNull() else null
        ItemDetail(files, upgrades, seasons)
    }
    fun setSeason(season: Int, monitored: Boolean, search: Boolean = monitored) {
        viewModelScope.launch {
            _message.value = runCatching { repo.setSeasonMonitored(id, season, monitored, search = search && monitored).message }
                .getOrElse { it.friendlyMessage() }
            refresh()
        }
    }
    fun consumeMessage() { _message.value = null }
}
