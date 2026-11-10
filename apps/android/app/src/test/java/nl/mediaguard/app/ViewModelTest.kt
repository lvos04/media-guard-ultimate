package nl.mediaguard.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonElement
import nl.mediaguard.app.data.AppStatus
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
import nl.mediaguard.app.ui.LibrarySearchViewModel
import nl.mediaguard.app.ui.StatusViewModel
import nl.mediaguard.app.ui.UiState
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException

/** Nep-MediaGuard voor de ViewModels: alleen wat de test nodig heeft. */
private open class FakeRepo : Repository {
    var statusResult: () -> AppStatus = { AppStatus(version = "1.2.1") }
    val libraryCalls = mutableListOf<String?>()
    val actions = mutableListOf<String>()
    override suspend fun me() = Me("jan", "operator")
    override suspend fun status() = statusResult()
    override suspend fun summary(date: String?) = DailySummary(date = date ?: "")
    override suspend fun recent() = DailySummary(window = "24h")
    override suspend fun paired(ntfyTopic: String?, appVersion: String?) = PairedResult(true)
    override suspend fun runAction(action: AttentionAction): String { actions += action.path; return "Gedaan" }
    override suspend fun systemUpdate(): JsonElement? = null
    override suspend fun control(action: String, params: Map<String, JsonElement>, confirm: Boolean) = ControlResult(true, "ok")
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
    override suspend fun missingCount() = 3
    override suspend fun libraryFiles(id: Long) = LibraryFiles()
    override suspend fun libraryUpgrades(id: Long) = UpgradeHistory()
    override suspend fun librarySeasons(id: Long) = SeasonMatrix()
    override suspend fun setSeasonMonitored(id: Long, season: Int, monitored: Boolean, search: Boolean) = MonitorResult()
}

@OptIn(ExperimentalCoroutinesApi::class)
class ViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    @Test fun statusRemembersLastSuccessAndMarksStale() {
        var now = 1_000_000L
        val repo = FakeRepo()
        val vm = StatusViewModel(repo, appVersion = "1.2.1", clock = { now })
        vm.refresh()
        assertEquals(1_000_000L, vm.freshness.value.lastOkAt)
        assertNull(Staleness.staleSince(vm.freshness.value, now))
        // MediaGuard valt weg: de oude stand blijft zichtbaar, maar als verouderd
        repo.statusResult = { throw IOException("weg") }
        now += 20_000
        vm.refresh()
        assertTrue(vm.state.value is UiState.Data)
        assertNotNull(vm.freshness.value.error)
        assertEquals(1_000_000L, Staleness.staleSince(vm.freshness.value, now))
        // weer bereikbaar
        repo.statusResult = { AppStatus(version = "1.2.1") }
        now += 20_000
        vm.refresh()
        assertNull(vm.freshness.value.error)
        assertNull(Staleness.staleSince(vm.freshness.value, now))
    }

    @Test fun newerServerMeansNewerApp() {
        val repo = FakeRepo().apply { statusResult = { AppStatus(version = "1.2.3") } }
        val vm = StatusViewModel(repo, appVersion = "1.2.1")
        vm.refresh()
        assertEquals("1.2.3", vm.update.value?.appUpdate)
    }

    @Test fun attentionActionRunsAndRefreshes() {
        val repo = FakeRepo()
        val vm = StatusViewModel(repo)
        vm.runAction(AttentionAction("Opnieuw testen", path = "/storage/probe"))
        assertEquals(listOf("/storage/probe"), repo.actions)
        assertEquals("Gedaan", vm.message.value)
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
