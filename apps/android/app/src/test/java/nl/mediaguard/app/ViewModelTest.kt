package nl.mediaguard.app

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonElement
import nl.mediaguard.app.data.AppStatus
import nl.mediaguard.app.data.Attention
import nl.mediaguard.app.data.AttentionItem
import nl.mediaguard.app.data.AttentionAction
import nl.mediaguard.app.data.ControlResult
import nl.mediaguard.app.data.DailySummary
import nl.mediaguard.app.data.LibraryFiles
import nl.mediaguard.app.data.LibraryPage
import nl.mediaguard.app.data.Me
import nl.mediaguard.app.data.MonitorResult
import nl.mediaguard.app.data.MyRequests
import nl.mediaguard.app.data.PairedResult
import nl.mediaguard.app.data.Repository
import nl.mediaguard.app.data.SeasonMatrix
import nl.mediaguard.app.data.SeerrDetail
import nl.mediaguard.app.data.SeerrPage
import nl.mediaguard.app.data.SeerrRequestResult
import nl.mediaguard.app.data.Staleness
import nl.mediaguard.app.data.UpgradeHistory
import nl.mediaguard.app.ui.summaryStaleSince
import nl.mediaguard.app.ui.screens.panelStatus
import nl.mediaguard.app.ui.PeilStatus
import nl.mediaguard.app.ui.LibrarySearchViewModel
import nl.mediaguard.app.ui.StatusViewModel
import nl.mediaguard.app.ui.UiState
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import okhttp3.ResponseBody.Companion.toResponseBody
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException
import java.time.Instant
import kotlin.time.Duration.Companion.milliseconds
import kotlin.time.TestTimeSource

/** Nep-MediaGuard voor de ViewModels: alleen wat de test nodig heeft. */
internal open class FakeRepo : Repository {
    var statusResult: () -> AppStatus = { AppStatus(version = "1.2.1") }
    var missingResult: suspend () -> Int = { 3 }
    var recentResult: () -> DailySummary = { DailySummary(date = "2026-11-07") }
    val libraryCalls = mutableListOf<String?>()
    val actions = mutableListOf<String>()
    val controls = mutableListOf<Pair<String, Map<String, JsonElement>>>()
    override suspend fun me() = Me("jan", "operator")
    override suspend fun status() = statusResult()
    override suspend fun summary(date: String?) = DailySummary(date = date ?: "")
    override suspend fun recent() = recentResult()
    override suspend fun paired(ntfyTopic: String?, appVersion: String?) = PairedResult(true)
    override suspend fun runAction(action: AttentionAction): String { actions += action.path; return "Gedaan" }
    override suspend fun systemUpdate(): JsonElement? = null
    override suspend fun control(action: String, params: Map<String, JsonElement>, confirm: Boolean): ControlResult {
        controls += action to params
        return ControlResult(true, "ok")
    }
    override suspend fun seerrSearch(q: String) = SeerrPage()
    override suspend fun seerrTrending() = SeerrPage()
    override suspend fun seerrDetail(type: String, id: Long) = SeerrDetail()
    override suspend fun seerrRequest(type: String, id: Long, seasons: List<Int>?) = SeerrRequestResult()
    override suspend fun myRequests(filter: String) = MyRequests()
    override suspend fun librarySearch(q: String) = LibraryPage()
    override suspend fun libraryList(q: String?, kind: String?, mediaType: String?, pageSize: Int, state: String?): LibraryPage {
        libraryCalls += "${kind ?: "-"}|${state ?: "-"}"
        return LibraryPage(total = if (state == "missing") 3 else 10)
    }
    override suspend fun missingCount() = missingResult()
    override suspend fun libraryFiles(id: Long) = LibraryFiles()
    override suspend fun libraryUpgrades(id: Long) = UpgradeHistory()
    override suspend fun librarySeasons(id: Long) = SeasonMatrix()
    override suspend fun setSeasonMonitored(id: Long, season: Int, monitored: Boolean, search: Boolean) = MonitorResult()
}

@OptIn(ExperimentalCoroutinesApi::class)
class ViewModelTest {
    private val elapsed = TestTimeSource()

    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    @Test fun statusRemembersLastSuccessAndMarksStale() {
        var now = 1_000_000L
        fun response() = AppStatus(version = "1.2.1", generatedAt = Instant.ofEpochMilli(now).toString())
        val repo = FakeRepo().apply { statusResult = ::response }
        val vm = StatusViewModel(repo, appVersion = "1.2.1", clock = { now }, elapsed = elapsed)
        fun freshness() = (vm.state.value as UiState.Data).freshness
        vm.refresh()
        assertEquals(1_000_000L, freshness().lastOkAt)
        assertNull(Staleness.staleSince(freshness(), now))
        repo.statusResult = { throw IOException("weg") }
        now += 20_000
        vm.refresh()
        assertTrue(vm.state.value is UiState.Data)
        assertNotNull(freshness().error)
        assertEquals(1_000_000L, Staleness.staleSince(freshness(), now))
        repo.statusResult = ::response
        now += 20_000
        vm.refresh()
        assertNull(freshness().error)
        assertNull(Staleness.staleSince(freshness(), now))
    }

    @Test fun verseOntvangstVerjongtOudRapportNiet() {
        val now = 1_000_000L
        val old = now - Staleness.MAX_AGE_MS
        val repo = FakeRepo()
        val vm = StatusViewModel(repo, clock = { now }, elapsed = elapsed)
        repo.statusResult = { AppStatus(generatedAt = Instant.ofEpochMilli(old).toString()) }
        vm.refresh()
        assertEquals(old, panelStatus(vm.state.value, now).staleSince)
        for (invalid in listOf(null, "ongeldig", Instant.ofEpochMilli(now + 60_000).toString())) {
            repo.statusResult = { AppStatus(generatedAt = invalid) }
            vm.refresh()
            assertEquals(PeilStatus.Onbekend, panelStatus(vm.state.value, now).total)
        }
    }

    @Test fun rollendVensterHeeftEigenEindtijdEnOudereServerEigenLeestijd() {
        val now = 1_000_000L
        val end = now - 2 * Staleness.SUMMARY_INTERVAL_MS
        val repo = FakeRepo()
        val vm = StatusViewModel(repo, clock = { now }, elapsed = elapsed)
        repo.recentResult = { DailySummary(window = "24h", windowEnd = Instant.ofEpochMilli(end).toString()) }
        vm.refresh()
        assertEquals(end, (vm.recent.value as UiState.Data).summaryStaleSince(now))
        repo.recentResult = { DailySummary(window = "24h", windowEnd = "ongeldig") }
        vm.refresh(forceExtras = true)
        assertNull((vm.recent.value as UiState.Data).freshness.lastOkAt)
        repo.recentResult = { DailySummary(date = "2026-11-07") }
        vm.refresh(forceExtras = true)
        assertEquals(now, (vm.recent.value as UiState.Data).freshness.lastOkAt)
    }

    @Test fun overzichtVerverstElkeBronOpEigenIntervalEnStoptOpDeAchtergrond() = runTest {
        val repo = FakeRepo()
        var statuses = 0
        var summaries = 0
        var counts = 0
        repo.statusResult = { statuses++; AppStatus() }
        repo.recentResult = { summaries++; DailySummary() }
        repo.missingResult = { ++counts }
        val vm = StatusViewModel(repo, clock = { testScheduler.currentTime })
        vm.startPolling()
        vm.startPolling()
        advanceTimeBy(Staleness.SUMMARY_INTERVAL_MS)
        runCurrent()
        assertEquals(16, statuses)
        assertEquals(2, summaries)
        assertEquals(2, counts)
        vm.stopPolling()
        advanceTimeBy(Staleness.SUMMARY_INTERVAL_MS)
        runCurrent()
        assertEquals(16, statuses)
        assertEquals(2, summaries)
        vm.refresh(forceExtras = true)
        assertEquals(3, summaries)
        assertEquals(3, counts)
    }

    @Test fun bronfoutBewaartEigenMeettijdEnWeigeringVerwijdertVorigeWaarde() {
        var now = 1_000_000L
        val repo = FakeRepo()
        val vm = StatusViewModel(repo, clock = { now }, elapsed = elapsed)
        vm.refresh()
        now += 20_000
        repo.missingResult = { throw IOException("proef") }
        vm.refresh(forceExtras = true)
        val count = vm.missing.value as UiState.Data
        assertEquals(3, count.value)
        assertEquals(1_000_000L, count.summaryStaleSince(now))
        assertNull((vm.recent.value as UiState.Data).summaryStaleSince(now))
        val forbidden = HttpException(Response.error<Unit>(403, "".toResponseBody()))
        repo.missingResult = { throw forbidden }
        repo.recentResult = { throw IOException("proef") }
        vm.refresh(forceExtras = true)
        assertTrue(vm.missing.value is UiState.Error)
        assertEquals(now, (vm.recent.value as UiState.Data).summaryStaleSince(now))
        repo.statusResult = { throw forbidden }
        repo.recentResult = { throw forbidden }
        vm.refresh(forceExtras = true)
        assertTrue(vm.state.value is UiState.Error)
        assertTrue(vm.recent.value is UiState.Error)
        repo.missingResult = { 4 }
        vm.refresh(forceExtras = true)
        assertEquals(4, (vm.missing.value as UiState.Data).value)
    }

    @Test fun tegelijkVerversenDeeltDeLopendeLezingPerBron() {
        val pending = CompletableDeferred<Int>()
        var calls = 0
        val repo = FakeRepo().apply { missingResult = { calls++; pending.await() } }
        val vm = StatusViewModel(repo)
        vm.refresh(forceExtras = true)
        vm.refresh(forceExtras = true)
        assertEquals(1, calls)
        assertTrue(vm.recent.value is UiState.Data)
        pending.complete(7)
        assertEquals(7, (vm.missing.value as UiState.Data).value)
    }

    @Test fun newerServerMeansNewerApp() {
        val repo = FakeRepo().apply { statusResult = { AppStatus(version = "1.2.3") } }
        val vm = StatusViewModel(repo, appVersion = "1.2.1")
        vm.refresh()
        assertEquals("1.2.3", vm.update.value?.appUpdate)
    }

    @Test fun attentionActionRunsAndRefreshes() {
        var now = 1_000_000L
        val action = AttentionAction("Opnieuw testen", path = "/storage/probe")
        val report = AppStatus(generatedAt = Instant.ofEpochMilli(now).toString(),
            attention = Attention(count = 1, items = listOf(AttentionItem(actions = listOf(action), href = "/?job=3"))))
        val repo = FakeRepo().apply { statusResult = { report } }
        val vm = StatusViewModel(repo, clock = { now }, elapsed = elapsed)
        assertFalse(vm.runAction(action))
        vm.refresh()
        assertTrue(vm.runAction(action))
        assertEquals(listOf("/storage/probe"), repo.actions)
        assertEquals("Gedaan", vm.message.value)
        // De bevestiging stond open toen de meting verliep of toen de actie uit de lijst verdween.
        now += Staleness.MAX_AGE_MS
        elapsed += Staleness.MAX_AGE_MS.milliseconds
        assertFalse(vm.runAction(action))
        assertFalse(vm.retryJob(3))
        repo.statusResult = { AppStatus(generatedAt = Instant.ofEpochMilli(now).toString()) }
        vm.refresh()
        assertFalse(vm.runAction(action))
        assertFalse(vm.retryJob(3))
        assertEquals(listOf("/storage/probe"), repo.actions)
    }

    @Test fun missingTileOpensFilteredList() {
        val repo = FakeRepo()
        val vm = LibrarySearchViewModel(repo, missingOnly = true)
        assertEquals("-|missing", repo.libraryCalls.last()) // alle soorten, alleen ontbrekend
        assertEquals(3, (vm.state.value as UiState.Data).value.total)
        vm.setMissingOnly(false)
        assertEquals("series|-", repo.libraryCalls.last())
    }
}
