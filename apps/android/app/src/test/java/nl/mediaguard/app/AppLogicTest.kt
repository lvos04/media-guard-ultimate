package nl.mediaguard.app

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import nl.mediaguard.app.data.ActionPaths
import nl.mediaguard.app.data.AppStatus
import nl.mediaguard.app.data.Attention
import nl.mediaguard.app.data.AttentionAction
import nl.mediaguard.app.data.DailySummary
import nl.mediaguard.app.data.EmergencyStop
import nl.mediaguard.app.data.Freshness
import nl.mediaguard.app.data.InMemoryPrefs
import nl.mediaguard.app.data.KeyRejection
import nl.mediaguard.app.data.Lane
import nl.mediaguard.app.data.MgJson
import nl.mediaguard.app.data.NetGuard
import nl.mediaguard.app.data.NtfyInfo
import nl.mediaguard.app.data.PairingPayload
import nl.mediaguard.app.data.PushRegistration
import nl.mediaguard.app.data.ResilientPrefs
import nl.mediaguard.app.data.RunningEncode
import nl.mediaguard.app.data.SettingsStore
import nl.mediaguard.app.data.Staleness
import nl.mediaguard.app.data.StateStore
import nl.mediaguard.app.data.UpdateInfo
import nl.mediaguard.app.data.Versions
import nl.mediaguard.app.ui.Tone
import nl.mediaguard.app.ui.screens.HeroModel
import nl.mediaguard.app.ui.screens.PairingReason
import nl.mediaguard.app.ui.screens.deliveryLabel
import nl.mediaguard.app.ui.PeilStatus
import nl.mediaguard.app.ui.screens.heroFor
import nl.mediaguard.app.ui.screens.httpsVariant
import nl.mediaguard.app.ui.screens.laneSummary
import nl.mediaguard.app.ui.screens.pairingReasonText
import nl.mediaguard.app.ui.screens.recentTitle
import nl.mediaguard.app.work.Schedules
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress
import java.security.GeneralSecurityException
import java.time.ZoneId
import java.time.ZonedDateTime

class AppLogicTest {
    // -- 22: beschadigde sleutelopslag ------------------------------------------------------------
    @Test fun corruptStoreAtOpenIsWipedAndRecovered() {
        var opens = 0
        var wiped = 0
        val prefs = ResilientPrefs(
            open = { opens++; if (opens == 1) throw SecurityException("Could not decrypt value") else InMemoryPrefs() },
            wipe = { wiped++ },
        )
        val s = SettingsStore(prefs)
        assertTrue(s.recovered)
        assertFalse(s.volatileOnly)
        assertEquals(1, wiped)
        assertFalse(s.isPaired) // gewist: de app vraagt om opnieuw te koppelen, geen crash
        s.serverUrl = "https://mg.example.nl/"
        assertEquals("https://mg.example.nl/", s.serverUrl)
    }

    @Test fun corruptValueOnReadDoesNotCrash() {
        var broken = true
        val bad = object : android.content.SharedPreferences by InMemoryPrefs() {
            override fun getString(key: String?, defValue: String?): String? =
                if (broken) throw SecurityException("Could not decrypt value") else defValue
        }
        var opens = 0
        val prefs = ResilientPrefs(open = { opens++; if (opens == 1) bad else InMemoryPrefs() }, wipe = { broken = false })
        val s = SettingsStore(prefs)
        assertNull(s.apiKey) // eerste lezing faalt → opnieuw aangemaakt, standaardwaarde
        assertTrue(s.recovered)
        s.apiKey = "mgk_nieuw"
        assertEquals("mgk_nieuw", s.apiKey)
    }

    @Test fun keystoreBrokenFallsBackToMemory() {
        val prefs = ResilientPrefs(open = { throw GeneralSecurityException("keystore kapot") }, wipe = {})
        val s = SettingsStore(prefs)
        assertTrue(s.recovered && s.volatileOnly)
        s.apiKey = "mgk_x"
        assertEquals("mgk_x", s.apiKey)
        assertTrue(pairingReasonText(PairingReason.StorageReset, volatileOnly = true)!!.contains("alleen bewaard"))
    }

    // -- 2: telefoonkanaal ----------------------------------------------------------------------
    @Test fun pairingWithTopicAlwaysPollsNtfy() {
        val s = SettingsStore(ResilientPrefs({ InMemoryPrefs() }, {}))
        s.savePairing(PairingPayload(url = "https://mg/", key = "mgk_a", ntfy = NtfyInfo("https://ntfy.sh", "mediaguard-x", enabled = false)))
        assertTrue(s.ntfyEnabled) // MediaGuard zet het kanaal aan via POST /app/paired
        s.ntfySince = "abc"
        s.saveNtfy(NtfyInfo("https://ntfy.sh", "mediaguard-y", true))
        assertEquals("mediaguard-y", s.ntfyTopic)
        assertNull(s.ntfySince) // nieuw onderwerp: niets overslaan
        s.savePairing(PairingPayload(url = "https://mg/", key = "mgk_b"))
        assertFalse(s.ntfyEnabled)
    }

    // -- 19: ingetrokken sleutel → koppelscherm ------------------------------------------------
    @Test fun only401OfCurrentKeyConfirmedByServerRejects() = kotlinx.coroutines.test.runTest {
        var current: String? = "mgk_nieuw"
        var serverSaysGone = true
        val gate = KeyRejection({ current }, { false }, { serverSaysGone })
        assertFalse(gate.check("mgk_oud")) // oude sleutel na opnieuw koppelen: telt niet
        assertTrue(gate.check("mgk_nieuw"))
        serverSaysGone = false // losse 401 elders, /auth/me werkt nog
        assertFalse(gate.check("mgk_nieuw"))
        val repaired = KeyRejection({ current }, { false }, { current = "mgk_nog_nieuwer"; true })
        assertFalse(repaired.check("mgk_nieuw")) // tijdens de controle opnieuw gekoppeld
        assertFalse(KeyRejection({ "k" }, { true }, { true }).check("k")) // al afgehandeld
    }

    // -- 10: verouderde gegevens en geen contact ------------------------------------------------
    @Test fun stalenessAndNoContactAlert() {
        val now = 1_762_600_000_000L
        assertNull(Staleness.staleSince(Freshness(), now))
        assertNull(Staleness.staleSince(Freshness(now - 30_000), now))
        assertEquals(now - 30_000, Staleness.staleSince(Freshness(now - 30_000, "weg"), now))
        assertEquals(now - 200_000, Staleness.staleSince(Freshness(now - 200_000), now))
        val h = 60 * 60 * 1000L
        assertFalse(Staleness.shouldAlertNoContact(0, 0, now))
        assertFalse(Staleness.shouldAlertNoContact(now - 2 * h, 0, now))
        assertTrue(Staleness.shouldAlertNoContact(now - 3 * h, 0, now))
        assertFalse(Staleness.shouldAlertNoContact(now - 3 * h, now - 3 * h, now)) // al gemeld
    }

    @Test fun stateStorePairingReset() {
        val st = StateStore(InMemoryPrefs())
        st.keyRejected = true
        st.storageReset = true
        st.contactAlertFor = 5
        st.lastContactAt = 9
        st.clearPairingState()
        assertFalse(st.keyRejected || st.storageReset)
        assertEquals(0L, st.contactAlertFor)
        assertEquals(9L, st.lastContactAt)
    }

    // -- 18: overzicht en knoppen ---------------------------------------------------------------
    @Test fun heroNeverSaysAllGoodDuringStopOrWhenStale() {
        val calm = AppStatus()
        assertEquals(HeroModel.Kind.Ok, heroFor(calm, null).kind)
        val stop = calm.copy(emergencyStop = EmergencyStop(true, "test", "2026-11-08T09:12:00Z"))
        val h = heroFor(stop, null)
        assertEquals("Noodstop actief", h.title)
        // een bewuste stop is geen gemeten falen: Gepauzeerd-vorm (Peil, ADR-0016 punt 9)
        assertEquals(PeilStatus.Gepauzeerd, h.status)
        assertEquals(PeilStatus.Gezond, heroFor(calm, null).status)
        val stale = heroFor(calm, 1_000L)
        assertEquals(HeroModel.Kind.Stale, stale.kind)
        assertTrue(stale.title.startsWith("Niet bijgewerkt sinds "))
        assertEquals(PeilStatus.Verouderd, stale.status)
        assertEquals(HeroModel.Kind.Waiting, heroFor(calm.copy(attention = Attention(count = 2)), null).kind)
    }

    @Test fun attentionItemsParseWithActions() {
        val json = """{"attention":{"count":1,"errors":1,"items":[{"key":"encoding.conflict","title_nl":"1 conflict",
            "detail_nl":"Kies per bestand.","severity":"error","href":"/quarantaine","count":1,"actions":[],
            "rows":[{"id":"12","title":"a.mkv","detail":null,"href":"/x","actions":[{"label_nl":"Origineel houden",
            "method":"post","path":"/encoding/jobs/12/cancel","body":{},"confirm_nl":"Het AV1-resultaat wordt weggegooid.","primary":false}]}],
            "more_rows":3,"admin_actions":1}]},
            "encoder":{"reachable":true,"lanes":[{"lane":"gpu","running":2,"slots":4,"paused":false}],
            "running":[{"job_id":null,"engine_job_id":"77","title":"x","lane":"gpu","percent":10,"eta_seconds":null}],"queued":0},
            "downloads":{"active":0,"clients_ok":null,"items":[]}}"""
        val s = MgJson.decodeFromString(AppStatus.serializer(), json)
        val item = s.attention.items.single()
        assertEquals("Kies per bestand.", item.detail)
        assertEquals(1, item.adminActions)
        assertEquals(3, item.moreRows)
        val act = item.rows.single().actions.single()
        assertTrue(act.needsConfirm)
        assertNull(s.encoder.running.single().jobId)
        assertNull(s.downloads.clientsOk)
    }

    @Test fun actionsThatTouchFilesNeedConfirm() {
        assertFalse(AttentionAction("Nu proberen", path = "/encoding/autopilot/run").needsConfirm)
        assertTrue(AttentionAction("Activeren", path = "/encoding/jobs/1/activate",
            body = JsonObject(mapOf("confirm" to JsonPrimitive(true)))).needsConfirm)
        assertTrue(AttentionAction("Opruimen", path = "/x", confirm = "Zeker?").needsConfirm)
        assertTrue(AttentionAction("Verplaatsen", path = "/routing/anime/actions/4/approve").needsConfirm) // verplaatst media
        assertFalse(AttentionAction("Opnieuw controleren", path = "/quarantine/12/release-check").needsConfirm)
        assertFalse(AttentionAction("Opnieuw testen", path = "/storage/probe").needsConfirm)
    }

    @Test fun actionPathsAreChecked() {
        assertEquals("encoding/jobs/12/activate", ActionPaths.safe("/encoding/jobs/12/activate"))
        assertNull(ActionPaths.safe("//evil.example/x"))
        assertNull(ActionPaths.safe("/a/../auth/setup"))
        assertNull(ActionPaths.safe("https://evil.example/x"))
        assertNull(ActionPaths.safe("/a?b=c"))
        assertNull(ActionPaths.safe("encoding/jobs"))
        assertEquals("Klaar", ActionPaths.message("""{"message_nl":"Klaar"}"""))
        assertEquals("Mag niet", ActionPaths.message("""{"detail":"Mag niet"}"""))
        assertNull(ActionPaths.message("""{"ok":true}"""))
    }

    // -- 21: cijfers ----------------------------------------------------------------------------
    @Test fun laneSummaryShowsRealCountsAndEta() {
        val (head, eta) = laneSummary(Lane("gpu", 2, 4), listOf(
            RunningEncode(1, "a", "gpu", 10.0, 3900), RunningEncode(2, "b", "gpu", 50.0, 540)))
        assertEquals("2 van 4 bezig", head)
        assertEquals("eerstvolgende klaar over 9m", eta)
        assertEquals("uit" to null, laneSummary(Lane("cpu", 0, 0), emptyList()))
        assertEquals("0 van 1 bezig · gepauzeerd", laneSummary(Lane("cpu", 0, 1, paused = true), emptyList()).first)
    }

    @Test fun deliveryStatesFromCoreGetLabels() {
        // Peil: geleverd is geen gezondheid (neutraal vinkje), niet gevonden is neutraal-sterk, vast = gemeten falen
        assertEquals(Tone.Muted, deliveryLabel("delivered", "Geleverd").soort)
        assertEquals(R.drawable.lucide_check, deliveryLabel("delivered", "Geleverd").icoon)
        assertEquals(Tone.Strong, deliveryLabel("not_found", "Niet gevonden").soort)
        assertEquals(R.drawable.lucide_calendar_clock, deliveryLabel("waiting_release", "Wacht op release").icoon)
        assertEquals(R.drawable.lucide_loader_circle, deliveryLabel("active", "Wordt gezocht").icoon)
        assertEquals(R.drawable.lucide_eye_off, deliveryLabel("stopped", "Gestopt").icoon)
        assertEquals(Tone.Bad, deliveryLabel("failed", "Mislukt").soort)
        assertEquals("Wordt gezocht", deliveryLabel("active", "Wordt gezocht").tekst)
    }

    @Test fun recentTitleFollowsWindow() {
        assertEquals("Laatste 24 uur", recentTitle(DailySummary(window = "24h", label = "Laatste 24 uur")))
        assertEquals("Gisteren", recentTitle(DailySummary(date = "2026-11-07"))) // oudere server: gisteren
    }

    // -- 20: vast tijdstip ----------------------------------------------------------------------
    @Test fun dailyTargetIsNextOccurrenceOfChosenTime() {
        val zone = ZoneId.of("Europe/Amsterdam")
        val now = ZonedDateTime.of(2026, 11, 8, 7, 30, 0, 0, zone)
        assertEquals(ZonedDateTime.of(2026, 11, 8, 8, 0, 0, 0, zone), Schedules.nextTarget(now, 8 * 60))
        assertEquals(ZonedDateTime.of(2026, 11, 9, 7, 0, 0, 0, zone), Schedules.nextTarget(now, 7 * 60))
        // precies op het tijdstip (de taak zelf): de volgende dag, nooit nog een keer vandaag
        assertEquals(ZonedDateTime.of(2026, 11, 9, 8, 0, 0, 0, zone),
            Schedules.nextTarget(ZonedDateTime.of(2026, 11, 8, 8, 0, 0, 0, zone), 8 * 60))
        // zomertijd: 08:00 blijft 08:00 lokale tijd
        val beforeDst = ZonedDateTime.of(2027, 3, 27, 9, 0, 0, 0, zone)
        assertEquals(8, Schedules.nextTarget(beforeDst, 8 * 60).hour)
    }

    // -- 13: http alleen in het eigen netwerk ----------------------------------------------------
    @Test fun localAddresses() {
        fun ip(s: String) = InetAddress.getByName(s)
        listOf("127.0.0.1", "10.1.2.3", "172.16.0.5", "192.168.1.20", "169.254.1.1", "100.100.1.1", "fd12::1", "::1")
            .forEach { assertTrue(it, NetGuard.isLocalAddress(ip(it))) }
        listOf("8.8.8.8", "104.16.1.1", "100.128.0.1", "2606:4700::1")
            .forEach { assertFalse(it, NetGuard.isLocalAddress(ip(it))) }
        assertEquals("https://mg.example.nl/", httpsVariant("http://mg.example.nl/"))
        assertNull(httpsVariant("https://mg.example.nl/"))
    }

    // -- 23: versies ----------------------------------------------------------------------------
    @Test fun versionsCompareNumerically() {
        assertTrue(Versions.newer("1.2.10", "1.2.9"))
        assertTrue(Versions.newer("v1.3.0", "1.2.9"))
        assertFalse(Versions.newer("1.2.1", "1.2.1"))
        assertFalse(Versions.newer("?", "1.2.1"))
        assertEquals(0, Versions.compare("1.2", "1.2.0"))
    }

    @Test fun updateInfoFromServerAndVersions() {
        // geen updatecontrole op de server: alleen app vs. server
        val plain = UpdateInfo.from(null, "1.2.3", "1.2.1")
        assertEquals("1.2.3", plain.appUpdate)
        assertNull(plain.serverUpdate)
        assertEquals("Nieuwere app beschikbaar: 1.2.3 (jij hebt 1.2.1)", plain.headline())
        // verschillende vormen van GET /system/update
        fun parse(json: String, server: String = "1.2.1", app: String = "1.2.1") =
            UpdateInfo.from(MgJson.parseToJsonElement(json), server, app)
        assertEquals("1.2.4", parse("""{"current":"1.2.1","latest":"1.2.4","update_available":true}""").serverUpdate)
        assertEquals("1.2.4", parse("""{"latest":{"version":"v1.2.4","url":"https://github.com/x"}}""").serverUpdate)
        assertEquals("1.2.5", parse("""{"release":{"tag_name":"v1.2.5","html_url":"https://github.com/r"}}""").serverUpdate)
        assertEquals("https://github.com/r", parse("""{"release":{"tag_name":"v1.2.5","html_url":"https://github.com/r"}}""").url)
        assertEquals("1.3.0", parse("""{"latest_version":"1.2.1","android":{"latest":"1.3.0"}}""").appUpdate)
        assertNull(parse("""{"latest":"1.2.1"}""").headline())
        assertNull(parse("""{"error":"geen internet"}""").headline())
        assertNull(parse("""[1,2]""").headline())
        assertNotNull(parse("""{"latest":"1.2.4"}""", app = "1.2.4").headline())
    }

    // -- 2: telefoonkanaal blijft het proberen tot MediaGuard het aanzet ---------------------------
    @Test fun pushRegistrationIsRetriedUntilItWorks() {
        val st = StateStore(InMemoryPrefs())
        assertTrue(st.pushPending) // gekoppeld vóór deze versie: ook aanzetten
        val now = 1_762_600_000_000L
        assertTrue(PushRegistration.shouldRetry(true, false, st.pushPending, st.pushAttemptAt, now))
        st.pushAttemptAt = now
        assertFalse(PushRegistration.shouldRetry(true, false, true, st.pushAttemptAt, now + 60_000)) // niet te vaak
        assertTrue(PushRegistration.shouldRetry(true, false, true, st.pushAttemptAt, now + PushRegistration.RETRY_MS))
        assertFalse(PushRegistration.shouldRetry(true, true, true, 0, now)) // sleutel geweigerd
        assertFalse(PushRegistration.shouldRetry(false, false, true, 0, now)) // niet gekoppeld
        st.pushPending = false
        assertFalse(PushRegistration.shouldRetry(true, false, st.pushPending, 0, now))
        st.clearPairingState() // opnieuw gekoppeld: weer proberen
        assertTrue(st.pushPending)
        assertEquals(0L, st.pushAttemptAt)
        fun http(code: Int) = retrofit2.HttpException(retrofit2.Response.error<Any>(code, "{}".toResponseBody(null)))
        assertTrue(PushRegistration.giveUp(http(403))) // viewer-sleutel: mag het nooit
        assertFalse(PushRegistration.giveUp(http(404))) // oudere server: later opnieuw
        assertFalse(PushRegistration.giveUp(java.io.IOException("weg")))
    }

    // -- 10: thuisadres onderweg is geen storing --------------------------------------------------
    @Test fun homeAddressAwayIsNoOutage() {
        assertTrue(Staleness.lanOnly("http://192.168.1.10:30080/"))
        assertTrue(Staleness.lanOnly("http://truenas.local:30080/"))
        assertTrue(Staleness.lanOnly("https://10.0.0.5/"))
        assertFalse(Staleness.lanOnly("https://mediaguard.example.nl/"))
        assertFalse(Staleness.lanOnly("https://104.16.1.1/"))
        assertFalse(Staleness.lanOnly(null))
        assertTrue(Staleness.expectedUnreachable("http://192.168.1.10:30080/", homeCapableNetwork = false))
        assertFalse(Staleness.expectedUnreachable("http://192.168.1.10:30080/", homeCapableNetwork = true))
        assertFalse(Staleness.expectedUnreachable("https://mediaguard.example.nl/", homeCapableNetwork = false))
    }

    // -- 23: vorm van GET /system/update (pakket release) ----------------------------------------
    @Test fun updateInfoFollowsServerCheckAndDismiss() {
        val body = """{"enabled":true,"current":"1.2.1","latest":"1.2.2","update_available":true,
            "url":"https://github.com/lvos04/media-guard-ultimate/releases/tag/v1.2.2","dismissed":false,
            "android":{"latest":"1.2.2","url":"https://github.com/lvos04/media-guard-ultimate/releases/download/v1.2.2/mediaguard-android-1.2.2.apk"}}"""
        val u = UpdateInfo.from(MgJson.parseToJsonElement(body), "1.2.1", "1.2.1+3")
        assertEquals("1.2.2", u.serverUpdate)
        assertEquals("1.2.2", u.appUpdate)
        assertEquals("MediaGuard 1.2.2 is uit (server en app)", u.headline())
        assertTrue(u.link!!.endsWith("/tag/v1.2.2"))
        // server al bijgewerkt, app nog niet: de knop gaat naar de APK
        val appOnly = UpdateInfo.from(MgJson.parseToJsonElement(body.replace("\"current\":\"1.2.1\"", "\"current\":\"1.2.2\"")), "1.2.2", "1.2.1")
        assertNull(appOnly.serverUpdate)
        assertTrue(appOnly.link!!.endsWith(".apk"))
        // in de web verborgen: geen melding over 1.2.2 meer, wel als de server nieuwer is dan de app
        val hidden = MgJson.parseToJsonElement(body.replace("\"dismissed\":false", "\"dismissed\":true"))
        assertNull(UpdateInfo.from(hidden, "1.2.1", "1.2.1").headline())
        assertEquals("1.2.2", UpdateInfo.from(hidden, "1.2.2", "1.2.1").appUpdate)
        // staat de update al in "Wacht op jou", dan op Overzicht alleen app-ouder-dan-server
        assertNull(u.withoutLatest().headline())
        assertEquals("1.2.3", UpdateInfo("1.2.3", "1.2.1", latest = "1.2.4").withoutLatest().appUpdate)
        // verbergen is geen bestandsactie: geen extra bevestiging
        assertFalse(AttentionAction("Verbergen tot de volgende versie", path = "/system/update/dismiss").needsConfirm)
    }
}
