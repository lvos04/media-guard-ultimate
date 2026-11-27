package nl.mediaguard.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import nl.mediaguard.app.data.AppStatus
import nl.mediaguard.app.data.Attention
import nl.mediaguard.app.data.AttentionAction
import nl.mediaguard.app.data.AttentionItem
import nl.mediaguard.app.data.DailySummary
import nl.mediaguard.app.data.MgJson
import nl.mediaguard.app.data.Staleness
import nl.mediaguard.app.ui.PeilStatus
import nl.mediaguard.app.ui.StatusViewModel
import nl.mediaguard.app.ui.UiState
import nl.mediaguard.app.ui.screens.panelStatus
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException
import java.time.Instant
import kotlin.time.Duration.Companion.milliseconds
import kotlin.time.TestTimeSource

@OptIn(ExperimentalCoroutinesApi::class)
class StatusValidatieTest {
    private val elapsed = TestTimeSource()
    private val begin = 1_000_000L
    private val actie = AttentionAction("Controle", path = "/storage/probe", confirm = "Controle uitvoeren?")

    private fun rapport(tijd: Long, link: String? = null) = AppStatus(
        generatedAt = Instant.ofEpochMilli(tijd).toString(),
        attention = Attention(items = listOf(AttentionItem(actions = listOf(actie), href = link))),
    )

    @Before fun start() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun stop() = Dispatchers.resetMain()

    @Test fun weigeringWistDrieBronnenEnBlokkeertBevestigen() {
        for (code in listOf(401, 403)) {
            val repo = FakeRepo().apply { statusResult = { rapport(begin, "/?job=7") } }
            val vm = StatusViewModel(repo, clock = { begin }, elapsed = elapsed)
            vm.refresh()
            assertTrue(listOf(vm.state.value, vm.missing.value, vm.recent.value).all { it is UiState.Data })
            val weigering = HttpException(Response.error<Unit>(code, "".toResponseBody()))
            repo.statusResult = { throw weigering }
            repo.missingResult = { throw weigering }
            repo.recentResult = { throw weigering }
            vm.refresh(forceExtras = true)
            assertTrue(listOf(vm.state.value, vm.missing.value, vm.recent.value).all { it is UiState.Error })
            assertFalse(vm.runAction(actie))
            assertFalse(vm.retryJob(7))
            assertTrue(repo.actions.isEmpty())
            assertTrue(repo.controls.isEmpty())
        }
    }

    @Test fun gewijzigdeInhoudVanEenBevestigingWordtGeweigerd() {
        val repo = FakeRepo().apply { statusResult = { rapport(begin) } }
        val vm = StatusViewModel(repo, clock = { begin }, elapsed = elapsed)
        vm.refresh()
        val ander = actie.copy(body = buildJsonObject {
            put("aantal", JsonPrimitive(2))
        })
        assertFalse(vm.runAction(ander))
        assertTrue(repo.actions.isEmpty())
        assertTrue(vm.runAction(actie))
        assertEquals(listOf(actie.path), repo.actions)
    }

    @Test fun leesfoutOfOngeldigeMeettijdHoudtEenOpenBevestigingTegen() {
        val eerste = rapport(begin, "/?job=7")
        val repo = FakeRepo().apply { statusResult = { eerste } }
        val vm = StatusViewModel(repo, clock = { begin }, elapsed = elapsed)
        vm.refresh()
        fun controleerWeigering() {
            assertFalse(vm.runAction(actie))
            assertFalse(vm.retryJob(7))
            assertTrue(repo.actions.isEmpty())
            assertTrue(repo.controls.isEmpty())
        }
        repo.statusResult = { throw IOException("lokale leesfout") }
        vm.refresh()
        controleerWeigering()
        for (tijd in listOf(null, "ongeldig", Instant.ofEpochMilli(begin + 60_000L).toString())) {
            repo.statusResult = { eerste.copy(generatedAt = tijd) }
            vm.refresh()
            controleerWeigering()
        }
    }

    @Test fun verlopenBevestigingBlijftVerlopenNaKlokcorrectie() {
        var nu = begin
        val repo = FakeRepo().apply { statusResult = { rapport(begin, "/?job=7") } }
        val vm = StatusViewModel(repo, clock = { nu }, elapsed = elapsed)
        vm.refresh()
        assertTrue(actie.needsConfirm)
        nu += Staleness.MAX_AGE_MS
        elapsed += Staleness.MAX_AGE_MS.milliseconds
        assertEquals(begin, panelStatus(vm.state.value, nu).staleSince)
        nu = begin - 120_000L
        assertFalse(vm.runAction(actie))
        assertFalse(vm.retryJob(7))
        assertTrue(repo.actions.isEmpty())
        assertTrue(repo.controls.isEmpty())
        assertEquals(begin, panelStatus(vm.state.value, nu).staleSince)
    }

    @Test fun alleenDeResterendeGeldigheidTeltOokBijHerhaaldeOntvangst() {
        var nu = begin + Staleness.MAX_AGE_MS - 10
        val repo = FakeRepo().apply { statusResult = { rapport(begin) } }
        val vm = StatusViewModel(repo, clock = { nu }, elapsed = elapsed)
        vm.refresh()
        nu = begin - 1_000L
        elapsed += 9.milliseconds
        assertNull(panelStatus(vm.state.value, nu).staleSince)
        elapsed += 1.milliseconds
        assertFalse(vm.runAction(actie))
        vm.refresh()
        assertFalse(vm.runAction(actie))
        assertTrue(repo.actions.isEmpty())

        repo.statusResult = { rapport(nu) }
        vm.refresh()
        assertTrue(vm.runAction(actie))
        assertEquals(listOf(actie.path), repo.actions)
    }

    @Test fun alleenEenGeldigeJobparameterMagEenHerhaalopdrachtStarten() {
        val repo = FakeRepo()
        val vm = StatusViewModel(repo, clock = { begin }, elapsed = elapsed)
        repo.statusResult = { rapport(begin, "/?job=7") }
        vm.refresh()
        assertTrue(vm.retryJob(7))
        val verwachteOpdracht = "retry_job" to mapOf("job_id" to JsonPrimitive(7L))
        assertEquals(listOf(verwachteOpdracht), repo.controls)
        val ongeldig = listOf("/?subjob=7", "/?job=7b", "/#job=7", "/?job=7&job=7", "/?job=7&tab=x",
            "/ander?job=7", "/?job=7#deel", "/?job=+7", "/?job=-7", "/?job=0", "/?job=9223372036854775808")
        for (link in ongeldig) {
            assertNull(link, AttentionItem(href = link).jobId)
            repo.statusResult = { rapport(begin, link) }
            vm.refresh()
            assertFalse(link, vm.retryJob(7))
        }
        assertEquals(listOf(verwachteOpdracht), repo.controls)
    }

    @Test fun ontbrekendeNoodstopmetingIsOnbekend() {
        val repo = FakeRepo()
        val vm = StatusViewModel(repo, clock = { begin }, elapsed = elapsed)
        fun paneel(noodstop: String) = run {
            val json = """{"generated_at":"${Instant.ofEpochMilli(begin)}","encoder":{"reachable":true},
                "downloads":{"clients_ok":true}$noodstop}"""
            repo.statusResult = { MgJson.decodeFromString(AppStatus.serializer(), json) }
            vm.refresh()
            panelStatus(vm.state.value, begin)
        }
        assertEquals(PeilStatus.Gezond, paneel(""","emergency_stop":{"active":false}""").total)
        assertEquals(PeilStatus.Gepauzeerd, paneel(""","emergency_stop":{"active":true}""").total)
        val uitslagen = listOf("", ""","emergency_stop":{}""", ""","emergency_stop":null""",
            ""","emergency_stop":{"active":null}""").map { veld ->
            paneel(veld).let { it.core to it.total }
        }
        val onbekend = PeilStatus.Onbekend to PeilStatus.Onbekend
        assertEquals(List(4) { onbekend }, uitslagen)
    }

    @Test fun legeOfOnbekendeDagmetingKrijgtGeenVerseTijd() {
        val repo = FakeRepo()
        val vm = StatusViewModel(repo, clock = { begin }, elapsed = elapsed)
        fun meettijd(dag: DailySummary): Long? {
            repo.recentResult = { dag }
            vm.refresh(forceExtras = true)
            return (vm.recent.value as UiState.Data).freshness.lastOkAt
        }
        val eindtijd = begin - 1_000
        assertEquals(eindtijd, meettijd(DailySummary(window = "24h", windowEnd = Instant.ofEpochMilli(eindtijd).toString())))
        assertEquals(begin, meettijd(DailySummary(date = "2024-02-29")))
        val leeg = MgJson.decodeFromString(DailySummary.serializer(), "{}")
        val onbekend = DailySummary(date = "2026-11-07", window = "onbekend")
        for (dag in listOf(leeg, onbekend, DailySummary(date = "2026-02-29"), onbekend.copy(window = "24h"))) {
            assertNull(meettijd(dag))
        }
    }
}
