package nl.mediaguard.app.work

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.serialization.Serializable
import nl.mediaguard.app.MainActivity
import nl.mediaguard.app.MediaGuardApp
import nl.mediaguard.app.PhoneChecks
import nl.mediaguard.app.R
import nl.mediaguard.app.data.ApiClient
import nl.mediaguard.app.data.CleartextBlocked
import nl.mediaguard.app.data.DailySummary
import nl.mediaguard.app.data.MgJson
import nl.mediaguard.app.data.SettingsStore
import nl.mediaguard.app.data.Staleness
import nl.mediaguard.app.data.StateStore
import nl.mediaguard.app.data.formatBytes
import nl.mediaguard.app.data.friendlyMessage
import nl.mediaguard.app.data.isUnauthorized
import nl.mediaguard.app.data.summaryLine
import nl.mediaguard.app.data.yesterday
import okhttp3.Request
import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit

object Schedules {
    /** Oude periodieke taak (tot 1.2.1): liep op wisselende tijden; wordt bij elke start opgeruimd. */
    private const val DAILY_OLD = "mg-daily-summary"
    const val DAILY = "mg-daily-summary-at"
    private const val NTFY = "mg-ntfy-poll"
    private const val NTFY_NOW = "mg-ntfy-now"
    private const val CONTACT = "mg-contact-check"
    const val KEY_TARGET = "target_at"
    /** Komt het dagoverzicht later dan dit (toestel uit, diepe slaap), dan slaat de app die dag over. */
    const val LATE_LIMIT_MS = 4 * 60 * 60 * 1000L

    private fun net() = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    /**
     * Plant alles volgens de instellingen. Het dagoverzicht is een eenmalige taak met een
     * startvertraging tot het gekozen tijdstip; de taak plant zelf de volgende dag. Met
     * [rescheduleDaily] (nieuw tijdstip of net gekoppeld) wordt de geplande taak vervangen.
     */
    fun apply(context: Context, s: SettingsStore, st: StateStore, rescheduleDaily: Boolean = false) {
        val wm = WorkManager.getInstance(context)
        wm.cancelUniqueWork(DAILY_OLD)
        val active = s.isPaired && !st.keyRejected
        if (active && s.dailyEnabled) {
            val now = System.currentTimeMillis()
            val stored = st.dailyTargetAt
            val wanted = nextTarget(ZonedDateTime.now(), s.dailyMinutes).toInstant().toEpochMilli()
            val fresh = rescheduleDaily || stored == 0L || stored < now - LATE_LIMIT_MS ||
                !sameTimeOfDay(stored, s.dailyMinutes)
            if (fresh) enqueueDaily(context, st, wanted, ExistingWorkPolicy.REPLACE)
            else enqueueDaily(context, st, stored, ExistingWorkPolicy.KEEP)
        } else {
            wm.cancelUniqueWork(DAILY)
            st.dailyTargetAt = 0
        }
        if (active && s.ntfyEnabled && !s.ntfyTopic.isNullOrBlank()) {
            val req = PeriodicWorkRequestBuilder<NtfyPollWorker>(15, TimeUnit.MINUTES).setConstraints(net()).build()
            wm.enqueueUniquePeriodicWork(NTFY, ExistingPeriodicWorkPolicy.KEEP, req)
        } else wm.cancelUniqueWork(NTFY)
        if (active) {
            val req = PeriodicWorkRequestBuilder<ContactWorker>(30, TimeUnit.MINUTES).setConstraints(net()).build()
            wm.enqueueUniquePeriodicWork(CONTACT, ExistingPeriodicWorkPolicy.KEEP, req)
        } else wm.cancelUniqueWork(CONTACT)
    }

    private fun enqueueDaily(context: Context, st: StateStore, target: Long, policy: ExistingWorkPolicy) {
        val delay = (target - System.currentTimeMillis()).coerceAtLeast(0)
        val req = OneTimeWorkRequestBuilder<DailySummaryWorker>()
            .setInitialDelay(delay, TimeUnit.MILLISECONDS)
            .setConstraints(net())
            .setInputData(workDataOf(KEY_TARGET to target))
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(DAILY, policy, req)
        if (policy != ExistingWorkPolicy.KEEP) st.dailyTargetAt = target
    }

    /** Vanuit de taak zelf: de volgende dag achter de huidige (lopende) taak zetten. */
    internal fun chainNextDaily(context: Context, s: SettingsStore, st: StateStore) {
        val target = nextTarget(ZonedDateTime.now().plusMinutes(1), s.dailyMinutes).toInstant().toEpochMilli()
        enqueueDaily(context, st, target, ExistingWorkPolicy.APPEND_OR_REPLACE)
    }

    /** Eén keer extra ntfy ophalen over enkele seconden (na een testmelding). */
    fun pollNow(context: Context) {
        val req = OneTimeWorkRequestBuilder<NtfyPollWorker>().setInitialDelay(5, TimeUnit.SECONDS)
            .setConstraints(net()).build()
        WorkManager.getInstance(context).enqueueUniqueWork(NTFY_NOW, ExistingWorkPolicy.REPLACE, req)
    }

    fun cancel(context: Context) {
        val wm = WorkManager.getInstance(context)
        listOf(DAILY_OLD, DAILY, NTFY, NTFY_NOW, CONTACT).forEach { wm.cancelUniqueWork(it) }
    }

    /** Het eerstvolgende moment op [minutesOfDay] (tijdzone van [now]), altijd ná [now]. */
    fun nextTarget(now: ZonedDateTime, minutesOfDay: Int): ZonedDateTime {
        // Lokale kloktijd (niet "middernacht + minuten"): op de dag van de zomertijd blijft 08:00 ook 08:00.
        val time = LocalTime.of((minutesOfDay / 60).coerceIn(0, 23), minutesOfDay % 60)
        var next = now.toLocalDate().atTime(time).atZone(now.zone)
        if (!next.isAfter(now)) next = now.toLocalDate().plusDays(1).atTime(time).atZone(now.zone)
        return next
    }

    private fun sameTimeOfDay(epochMs: Long, minutesOfDay: Int, zone: ZoneId = ZoneId.systemDefault()): Boolean {
        val t = Instant.ofEpochMilli(epochMs).atZone(zone)
        return t.hour * 60 + t.minute == minutesOfDay
    }

    /** Minuten tot het volgende moment op [minutesOfDay]. */
    fun initialDelayMinutes(minutesOfDay: Int, now: LocalDateTime = LocalDateTime.now(ZoneId.of("Europe/Amsterdam"))): Long {
        val zoned = now.atZone(ZoneId.of("Europe/Amsterdam"))
        return Duration.between(zoned, nextTarget(zoned, minutesOfDay)).toMinutes().coerceAtLeast(1)
    }
}

internal fun canNotify(context: Context): Boolean =
    android.os.Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

private fun openAppIntent(context: Context, target: String): PendingIntent = PendingIntent.getActivity(
    context, target.hashCode(),
    Intent(context, MainActivity::class.java).putExtra(MainActivity.EXTRA_OPEN, target)
        .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
)

/** Meldingen die de app zelf maakt (zonder MediaGuard): koppeling verlopen, geen contact, test. */
object Alerts {
    const val ID_KEY_REJECTED = 2001
    const val ID_NO_CONTACT = 2002
    const val ID_TEST = 2003
    private val CLOCK = DateTimeFormatter.ofPattern("d MMM HH:mm", java.util.Locale.forLanguageTag("nl-NL"))

    private fun show(context: Context, id: Int, title: String, text: String, target: String) {
        if (!canNotify(context)) return
        runCatching {
            NotificationManagerCompat.from(context).notify(id, NotificationCompat.Builder(context, MediaGuardApp.CHANNEL_ALERTS)
                .setSmallIcon(R.drawable.ic_stat_mediaguard)
                .setContentTitle(title).setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setContentIntent(openAppIntent(context, target))
                .setAutoCancel(true).build())
        }
    }

    fun cancel(context: Context, id: Int) { runCatching { NotificationManagerCompat.from(context).cancel(id) } }

    fun keyRejected(context: Context) = show(context, ID_KEY_REJECTED, "Koppel MediaGuard opnieuw",
        "MediaGuard weigert de sleutel van deze app (ingetrokken of verlopen). Open de app en scan een nieuwe koppelcode.",
        "status")

    fun noContact(context: Context, lastContactAt: Long) = show(context, ID_NO_CONTACT, "MediaGuard reageert niet",
        "Geen contact sinds ${CLOCK.format(Instant.ofEpochMilli(lastContactAt).atZone(ZoneId.systemDefault()))}. " +
            "Controleer of de server en de tunnel draaien.", "status")

    fun localTest(context: Context, text: String) = show(context, ID_TEST, "MediaGuard: testmelding", text, "settings")

    /** Na een mislukte poging: melden als MediaGuard al [Staleness.NO_CONTACT_ALERT_MS] niets liet horen. */
    fun checkContact(context: Context, st: StateStore, now: Long = System.currentTimeMillis()) {
        val last = st.lastContactAt
        if (Staleness.shouldAlertNoContact(last, st.contactAlertFor, now)) {
            noContact(context, last)
            st.contactAlertFor = last
        }
    }
}

/** Haalt het overzicht van gisteren op het gekozen tijdstip op en toont een melding met de hoofdpunten. */
class DailySummaryWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as MediaGuardApp
        val repo = app.repository() ?: return Result.success() // ontkoppeld: geen volgende dag
        if (app.state.keyRejected) return Result.success() // eerst opnieuw koppelen
        val target = inputData.getLong(Schedules.KEY_TARGET, 0L)
        val late = target > 0 && System.currentTimeMillis() - target > Schedules.LATE_LIMIT_MS
        if (!late) {
            val summary = try {
                repo.summary(yesterday().toString())
            } catch (e: Exception) {
                // 401: niet blijven proberen (de app controleert de sleutel en vraagt zo nodig om opnieuw
                // te koppelen; dan vervalt ook de volgende dag). Anders een kwartier lang opnieuw.
                if (!e.isUnauthorized() && runAttemptCount < 5) return Result.retry()
                null // vandaag niet gelukt: morgen gewoon weer
            }
            if (summary != null && canNotify(applicationContext)) {
                NotificationManagerCompat.from(applicationContext).notify(1001, build(summary))
            }
        }
        if (app.settings.dailyEnabled && !app.state.keyRejected) Schedules.chainNextDaily(applicationContext, app.settings, app.state)
        return Result.success()
    }

    private fun build(s: DailySummary) = NotificationCompat.Builder(applicationContext, MediaGuardApp.CHANNEL_DAILY)
        .setSmallIcon(R.drawable.ic_stat_mediaguard)
        .setContentTitle("MediaGuard — gisteren")
        .setContentText(summaryLine(s))
        .setStyle(NotificationCompat.InboxStyle().also { st ->
            inboxLines(s).forEach { st.addLine(it) }
            st.setSummaryText(summaryLine(s))
        })
        .setContentIntent(openAppIntent(applicationContext, "today"))
        .setAutoCancel(true)
        .build()

    companion object {
        fun inboxLines(s: DailySummary): List<String> = buildList {
            if (s.headline.isNotBlank()) add(s.headline)
            add("${s.replaced.size} vervangen door AV1 · ${formatBytes(s.storage.savedBytes)} bespaard")
            add("${s.downloaded.size} gedownload (${s.downloaded.count { it.kind == "upgrade" }} upgrades)")
            if (s.requests.isNotEmpty()) add("${s.requests.size} aangevraagd")
            if (s.attention.isNotEmpty()) add("${s.attention.size} punt(en) vragen aandacht")
            add("Opslag netto ${formatBytes(s.storage.netBytes, signed = true)}")
        }.take(6)
    }
}

/** Elke 30 minuten: antwoordt MediaGuard nog? Zo niet, na 3 uur één melding (Staleness). */
class ContactWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as MediaGuardApp
        val repo = app.repository() ?: return Result.success()
        // Een antwoord (ook een 4xx) zet "laatste contact" via de ApiHooks; 401 opent het koppelscherm.
        if (runCatching { repo.me() }.isFailure) {
            // Thuisadres en de telefoon zit onderweg op mobiele data: geen storing, geen melding.
            val away = Staleness.expectedUnreachable(app.settings.serverUrl, PhoneChecks.onHomeCapableNetwork(applicationContext))
            if (!away) Alerts.checkContact(applicationContext, app.state)
        }
        return Result.success()
    }
}

@Serializable
data class NtfyMessage(
    val id: String = "",
    val event: String = "",
    val time: Long = 0,
    val title: String? = null,
    val message: String? = null,
    val priority: Int? = null,
)

/** Haalt nieuwe berichten van het ntfy-kanaal op (poll; geen Firebase nodig). */
class NtfyPollWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as MediaGuardApp
        val s = app.settings
        val st = app.state
        val topic = s.ntfyTopic ?: return Result.success()
        val server = (s.ntfyServer ?: "https://ntfy.sh").trimEnd('/')
        val since = s.ntfySince ?: "10m"
        val body = try {
            ApiClient.guarded().newCall(Request.Builder().url("$server/$topic/json?poll=1&since=$since").build())
                .execute().use { r ->
                    if (!r.isSuccessful) throw java.io.IOException("ntfy gaf fout ${r.code}")
                    r.body?.string().orEmpty()
                }
        } catch (e: Exception) {
            st.ntfyLastError = when (e) {
                is CleartextBlocked -> e.friendlyMessage()
                is java.io.IOException -> e.message?.takeIf { it.startsWith("ntfy") } ?: "ntfy-server niet bereikbaar"
                else -> e.message ?: "onbekende fout"
            }
            st.ntfyLastErrorAt = System.currentTimeMillis()
            return Result.retry()
        }
        st.ntfyLastOkAt = System.currentTimeMillis()
        st.ntfyLastError = null
        val messages = parse(body)
        if (messages.isNotEmpty()) {
            s.ntfySince = messages.last().id
            if (canNotify(applicationContext)) {
                val nm = NotificationManagerCompat.from(applicationContext)
                messages.takeLast(5).forEach { m ->
                    nm.notify(m.id.hashCode(), NotificationCompat.Builder(applicationContext, MediaGuardApp.CHANNEL_ALERTS)
                        .setSmallIcon(R.drawable.ic_stat_mediaguard)
                        .setContentTitle(m.title ?: "MediaGuard")
                        .setContentText(m.message ?: "")
                        .setStyle(NotificationCompat.BigTextStyle().bigText(m.message ?: ""))
                        .setContentIntent(openAppIntent(applicationContext, "status"))
                        .setAutoCancel(true).build())
                }
            }
        }
        return Result.success()
    }

    companion object {
        /** ntfy geeft één JSON-object per regel; alleen 'message'-events tellen. */
        fun parse(body: String): List<NtfyMessage> = body.lineSequence()
            .filter { it.isNotBlank() }
            .mapNotNull { runCatching { MgJson.decodeFromString(NtfyMessage.serializer(), it) }.getOrNull() }
            .filter { it.event == "message" }
            .toList()
    }
}
