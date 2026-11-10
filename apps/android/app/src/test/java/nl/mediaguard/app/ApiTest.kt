package nl.mediaguard.app

import kotlinx.coroutines.test.runTest
import nl.mediaguard.app.data.ApiClient
import nl.mediaguard.app.data.ApiRepository
import nl.mediaguard.app.data.CleartextBlocked
import nl.mediaguard.app.data.CleartextGuard
import nl.mediaguard.app.data.friendlyMessage
import nl.mediaguard.app.data.isUnauthorized
import nl.mediaguard.app.work.DailySummaryWorker
import nl.mediaguard.app.work.NtfyPollWorker
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** Parseert de voorbeelden uit docs/APP_API.md tegen een nep-MediaGuard. */
class ApiTest {
    private lateinit var server: MockWebServer
    private lateinit var repo: ApiRepository

    @Before fun setUp() {
        server = MockWebServer().also { it.start() }
        repo = ApiRepository(ApiClient.create(server.url("/").toString(), "mgk_test"))
    }

    @After fun tearDown() = server.shutdown()

    private fun json(body: String, code: Int = 200) =
        server.enqueue(MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body))

    @Test fun statusParsesAndSendsKey() = runTest {
        json("""{"version":"1.2.0","emergency_stop":{"active":false,"reason":"","since":null},
          "attention":{"count":3,"errors":1,"items":[{"key":"a","title_nl":"Iets","severity":"error","href":"/x"}]},
          "encoder":{"reachable":true,"lanes":[{"lane":"gpu","running":2,"slots":4,"paused":false,"reason_nl":null}],
            "running":[{"job_id":115,"title":"T","lane":"gpu","percent":42.5,"eta_seconds":900,"phase_nl":"Encoderen"}],"queued":8},
          "downloads":{"active":3,"clients_ok":true,"items":[{"title":"D","client":"SABnzbd","percent":10,"eta_seconds":60,"size":5}]},
          "storage":{"free_bytes":0,"days_left_nl":"± 300 dagen"},"savings_bytes":834000000000,"extra":"ignored"}""")
        val s = repo.status()
        assertEquals(2, s.encoder.lanes.first().running)
        assertEquals(42.5, s.encoder.running.first().percent!!, 0.001)
        assertEquals("Iets", s.attention.items.first().title)
        val req = server.takeRequest()
        assertEquals("/api/v1/app/status", req.path)
        assertEquals("mgk_test", req.getHeader("X-Api-Key"))
    }

    @Test fun summaryAndInboxLines() = runTest {
        json("""{"date":"2026-11-07","headline_nl":"Gisteren: 37 afleveringen","replaced":[{"title":"A","label":"S01E01","from_size":2,"to_size":1,"saved":1,"vmaf":95.5,"at":"2026-11-07T10:00:00Z"}],
          "downloaded":[{"title":"B","kind":"upgrade","size":3,"quality":"WEBDL-1080p","source_nl":"RSS","at":null}],
          "upgrades":[{"title":"B","label":"S01E02","reason_nl":"Betere custom formats","at":null}],
          "requests":[{"title":"C","media_type":"tv","requested_at":null,"delivery_state_nl":"Wordt gezocht"}],
          "attention":[],"storage":{"net_bytes":10,"added_bytes":20,"saved_bytes":10,"unexplained_bytes":0},
          "encoder":{"done":12,"failed":1,"saved_bytes":0}}""")
        val s = repo.summary("2026-11-07")
        assertEquals("/api/v1/app/summary?date=2026-11-07", server.takeRequest().path)
        assertEquals("upgrade", s.downloaded.first().kind)
        val lines = DailySummaryWorker.inboxLines(s)
        assertTrue(lines.any { it.contains("1 vervangen") })
        assertTrue(lines.any { it.contains("1 upgrades") })
    }

    @Test fun seerrRequestSendsSeasons() = runTest {
        json("""{"request_id":77,"status":"aangevraagd","message_nl":"Aangevraagd: 2 seizoenen"}""")
        val r = repo.seerrRequest("tv", 1399, listOf(1, 2))
        assertEquals("Aangevraagd: 2 seizoenen", r.message)
        val body = server.takeRequest().body.readUtf8()
        assertTrue(body, body.contains("\"seasons\":[1,2]"))
        json("""{"request_id":78}""")
        repo.seerrRequest("tv", 1399, null)
        assertTrue(server.takeRequest().body.readUtf8().contains("\"seasons\":\"all\""))
    }

    @Test fun libraryFilesAndUpgrades() = runTest {
        json("""{"item_id":12,"kind":"series","profile":{"name":"MediaGuard · Anime Dual Audio","cutoff":"Bluray-2160p","cutoff_score":2200},
          "files":[{"file_id":991,"label":"S01E03","size":1503238553,"quality":{"name":"WEBDL-1080p","resolution":1080},
          "media":{"video_codec":"AV1","dynamic_range":"HDR10","audio_languages":["jpn","eng"]},
          "custom_formats":[{"name":"MediaGuard · Dual Audio","score":2000}],"score":2000,"cutoff_met":true,"av1_by_mediaguard":true,
          "summary_nl":"WEBDL-1080p · AV1"}]}""")
        val f = repo.libraryFiles(12)
        assertTrue(f.files.first().av1ByMediaGuard)
        assertEquals(2200, f.profile.cutoffScore)
        server.takeRequest()
        json("""{"item_id":12,"upgrades":[{"at":"2026-11-07T04:12:00Z","label":"S01E03","old":{"quality":"WEBDL-1080p","score":1500,"av1_by_mediaguard":true},
          "new":{"quality":"WEBDL-1080p","score":2000,"release_group":"GRPA"},"source_nl":"RSS","reason_nl":"Betere custom formats"}]}""")
        val u = repo.libraryUpgrades(12)
        assertTrue(u.upgrades.first().old.av1ByMediaGuard)
        assertEquals("GRPA", u.upgrades.first().new.releaseGroup)
    }

    @Test fun errorDetailIsShown() = runTest {
        json("""{"detail":{"code":"CONFIRM_REQUIRED","message":"Bevestiging nodig"}}""", code = 409)
        val e = runCatching { repo.control("pause_lane") }.exceptionOrNull()!!
        assertEquals("Bevestiging nodig", e.friendlyMessage())
        json("""{"detail":"Ongeldige of ingetrokken API-sleutel"}""", code = 401)
        val e2 = runCatching { repo.me() }.exceptionOrNull()!!
        assertEquals("Ongeldige of ingetrokken API-sleutel", e2.friendlyMessage())
    }

    @Test fun ntfyParsesOnlyMessages() {
        val body = """{"id":"a","event":"open","time":1}
{"id":"b","event":"message","time":2,"title":"Fout","message":"AV1 staat stil"}
{"id":"c","event":"keepalive","time":3}"""
        val m = NtfyPollWorker.parse(body)
        assertEquals(1, m.size)
        assertEquals("AV1 staat stil", m.first().message)
    }

    @Test fun hooksSeeContactAndRejectedKey() = runTest {
        var contact = 0
        var rejected = 0
        val hooked = ApiRepository(ApiClient.create(server.url("/").toString(), "mgk_oud",
            hooks = nl.mediaguard.app.data.ApiHooks(onContact = { contact++ }, onUnauthorized = { rejected++ })))
        json("""{"username":"x","role":"operator"}""")
        hooked.me()
        assertEquals(1, contact)
        json("""{"detail":"Ongeldige of ingetrokken API-sleutel"}""", code = 401)
        val e = runCatching { hooked.me() }.exceptionOrNull()!!
        assertTrue(e.isUnauthorized())
        assertEquals(1, rejected)
        json("""{"detail":"kapot"}""", code = 502) // tunnel/proxy zonder core: geen contact
        runCatching { hooked.me() }
        assertEquals(1, contact)
    }

    @Test fun cleartextToPublicAddressIsBlocked() = runTest {
        val publicDns = object : okhttp3.Dns {
            override fun lookup(hostname: String) = listOf(java.net.InetAddress.getByName("104.16.1.1"))
        }
        val blocked = ApiRepository(ApiClient.create("http://mg.example.nl/", "mgk_test",
            client = okhttp3.OkHttpClient.Builder().dns(publicDns).build()))
        val e = runCatching { blocked.me() }.exceptionOrNull()
        assertTrue(e.toString(), e is CleartextBlocked)
        assertTrue(e!!.friendlyMessage().contains("https"))
        assertEquals(0, server.requestCount) // de sleutel ging nergens heen
        // naar het eigen netwerk (hier: deze machine) mag http wel
        json("""{"username":"x","role":"operator"}""")
        assertEquals("x", repo.me().username)
    }

    @Test fun recentFallsBackToYesterdayOnOldServer() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""))
        json("""{"date":"2026-11-07","headline_nl":"Gisteren: rustige dag, niets nieuws"}""")
        val s = repo.recent()
        assertEquals("/api/v1/app/recent", server.takeRequest().path)
        assertTrue(server.takeRequest().path!!.startsWith("/api/v1/app/summary?date="))
        assertEquals(null, s.window)
        json("""{"window":"24h","label_nl":"Laatste 24 uur","date":null,"headline_nl":"Laatste 24 uur: rustig",
          "storage":{"net_bytes":0,"added_bytes":0,"saved_bytes":0,"unexplained_bytes":null}}""")
        val r = repo.recent()
        assertEquals("24h", r.window)
        assertEquals(null, r.storage.unexplainedBytes)
    }

    @Test fun attentionActionPostsToSameEndpoint() = runTest {
        json("""{"message_nl":"Origineel blijft staan"}""")
        val msg = repo.runAction(nl.mediaguard.app.data.AttentionAction("Origineel houden", path = "/encoding/jobs/12/cancel",
            body = kotlinx.serialization.json.JsonObject(mapOf("confirm" to kotlinx.serialization.json.JsonPrimitive(true)))))
        assertEquals("Origineel blijft staan", msg)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/v1/encoding/jobs/12/cancel", req.path)
        assertEquals("""{"confirm":true}""", req.body.readUtf8())
        val bad = runCatching { repo.runAction(nl.mediaguard.app.data.AttentionAction("x", path = "/../auth/setup")) }
        assertTrue(bad.isFailure)
        assertEquals(1, server.requestCount)
    }

    @Test fun pairedAndUpdateCheck() = runTest {
        json("""{"ok":true,"ntfy":{"server":"https://ntfy.sh","topic":"mediaguard-x","enabled":true},"message_nl":"aan"}""")
        val r = repo.paired("mediaguard-x", "1.2.1")
        assertEquals("mediaguard-x", r.ntfy?.topic)
        assertTrue(server.takeRequest().body.readUtf8().contains("\"ntfy_topic\":\"mediaguard-x\""))
        server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
        assertEquals(null, repo.systemUpdate())
        json("""{"latest":"1.2.4"}""")
        assertEquals("1.2.4", nl.mediaguard.app.data.UpdateInfo.from(repo.systemUpdate(), "1.2.1", "1.2.1").serverUpdate)
    }

    @Test fun cleartextRedirectToOtherAddressIsBlocked() {
        // Een doorverwijzing naar een ander http-adres: OkHttp neemt X-Api-Key mee. De netwerk-interceptor
        // ziet het echte adres en stopt vóór er iets verstuurd wordt. (127.0.0.2 speelt hier "internet".)
        val outside = MockWebServer().also { it.start(java.net.InetAddress.getByName("127.0.0.2"), 0) }
        try {
            server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "http://127.0.0.2:${outside.port}/api/v1/auth/me"))
            val notOutside: (java.net.InetAddress) -> Boolean = { it.hostAddress != "127.0.0.2" }
            val http = okhttp3.OkHttpClient.Builder()
                .addInterceptor(CleartextGuard(isLocal = notOutside))
                .addNetworkInterceptor(CleartextGuard(isLocal = notOutside))
                .build()
            val call = http.newCall(okhttp3.Request.Builder().url(server.url("/api/v1/auth/me")).header("X-Api-Key", "mgk_geheim").build())
            val e = runCatching { call.execute().close() }.exceptionOrNull()
            assertTrue(e.toString(), e is CleartextBlocked)
            assertEquals(1, server.requestCount)
            assertEquals(0, outside.requestCount) // de sleutel ging niet naar het andere adres
            // de echte client heeft beide bewakers
            val guarded = ApiClient.guarded()
            assertTrue(guarded.interceptors.any { it is CleartextGuard })
            assertTrue(guarded.networkInterceptors.any { it is CleartextGuard })
        } finally {
            outside.shutdown()
        }
    }
}
