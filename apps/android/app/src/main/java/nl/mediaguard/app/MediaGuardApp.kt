package nl.mediaguard.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.res.Configuration
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.atomic.AtomicBoolean
import nl.mediaguard.app.data.ApiClient
import nl.mediaguard.app.data.ApiHooks
import nl.mediaguard.app.data.ApiRepository
import nl.mediaguard.app.data.AppTaal
import nl.mediaguard.app.data.KeyRejection
import nl.mediaguard.app.data.PairingPayload
import nl.mediaguard.app.data.PushRegistration
import nl.mediaguard.app.data.Repository
import nl.mediaguard.app.data.SettingsStore
import nl.mediaguard.app.data.StateStore
import nl.mediaguard.app.data.Taal
import nl.mediaguard.app.data.friendlyMessage
import nl.mediaguard.app.data.isUnauthorized
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.work.Alerts
import nl.mediaguard.app.work.Schedules

/**
 * Toestand van de koppeling voor de UI. [generation] gaat omhoog bij elke nieuwe koppeling of
 * ontkoppeling: de schermen (en hun ViewModels) beginnen dan opnieuw met de nieuwe sleutel, zonder
 * de app te hoeven afsluiten.
 */
data class Session(val generation: Int, val paired: Boolean, val rejected: Boolean)

/**
 * De app volgt de taal die Android voor deze app kiest: de systeemtaal, of vanaf Android 13 de taal per app.
 * `values/` is Nederlands en `values-en/` Engels; elke andere taal valt terug op Nederlands (ADR-0023).
 */
fun Context.volgAppTaal() {
    Taal.huidig = AppTaal.of(getString(R.string.app_taal))
}

class MediaGuardApp : Application() {
    lateinit var settings: SettingsStore
        private set
    lateinit var state: StateStore
        private set
    private var cached: Pair<String, Repository>? = null
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val _session = MutableStateFlow(Session(0, paired = false, rejected = false))
    val session: StateFlow<Session> = _session.asStateFlow()
    private val _notice = MutableStateFlow<String?>(null)
    /** Eenmalige mededeling voor de snackbar (bv. na het koppelen). */
    val notice: StateFlow<String?> = _notice.asStateFlow()

    override fun onCreate() {
        super.onCreate()
        volgAppTaal()
        settings = SettingsStore(this)
        state = StateStore(this)
        if (settings.recovered) { state.storageReset = true; state.keyRejected = false }
        createChannels()
        _session.value = Session(0, settings.isPaired, state.keyRejected)
        if (settings.isPaired) Schedules.apply(this, settings, state)
        retryPushIfPending()
    }

    /** Andere taal gekozen terwijl de app draait: teksten en de namen van de meldingskanalen volgen. */
    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        volgAppTaal()
        createChannels()
    }

    /** Repository voor de huidige koppeling; null zolang de app niet gekoppeld is. */
    @Synchronized
    fun repository(): Repository? {
        val url = settings.serverUrl ?: return null
        val key = settings.apiKey ?: return null
        val id = "$url|$key"
        cached?.let { if (it.first == id) return it.second }
        val hooks = ApiHooks(onContact = ::onContact, onUnauthorized = { onUnauthorized(key) })
        val repo = ApiRepository(ApiClient.create(url, key, hooks = hooks))
        cached = id to repo
        return repo
    }

    /** Nieuwe koppeling opslaan, alles opnieuw plannen en MediaGuard laten weten dat de app meeleest. */
    fun completePairing(payload: PairingPayload, role: String?) {
        settings.savePairing(payload)
        state.clearPairingState()
        state.lastContactAt = System.currentTimeMillis()
        Alerts.cancel(this, Alerts.ID_KEY_REJECTED)
        Alerts.cancel(this, Alerts.ID_NO_CONTACT)
        Schedules.apply(this, settings, state, rescheduleDaily = true)
        _session.update { Session(it.generation + 1, paired = true, rejected = false) }
        if (role == "viewer") _notice.value = tr("Gekoppeld met alleen-lezen rechten (viewer). Bediening werkt met een operator-sleutel.", "Linked with read-only rights (viewer). Controls need an operator key.")
        registerPush()
    }

    private val pushBusy = AtomicBoolean(false)

    /**
     * POST /app/paired: MediaGuard zet het telefoonkanaal aan en geeft het onderwerp (ook bij handmatig
     * koppelen). Lukt het niet (geen verbinding, server even weg), dan blijft [StateStore.pushPending]
     * staan en probeert de app het opnieuw bij de volgende start of het volgende contact. [onDone] krijgt
     * de uitkomst in gewone taal (op de hoofdthread).
     */
    fun registerPush(onDone: ((String) -> Unit)? = null) {
        val repo = repository() ?: return
        if (state.keyRejected || !pushBusy.compareAndSet(false, true)) return
        state.pushAttemptAt = System.currentTimeMillis()
        scope.launch {
            val result = try {
                runCatching { repo.paired(settings.ntfyTopic, BuildConfigInfo.versionName(this@MediaGuardApp)) }
            } finally { pushBusy.set(false) }
            result.onSuccess { r ->
                r.ntfy?.let { settings.saveNtfy(it) }
                state.pushRegisteredAt = System.currentTimeMillis()
                state.pushPending = false
                Schedules.apply(this@MediaGuardApp, settings, state)
            }.onFailure { e ->
                if (PushRegistration.giveUp(e)) state.pushPending = false
            }
            val text = result.fold(
                { it.message.ifBlank { tr("Pushmeldingen naar deze telefoon staan aan", "Push notifications to this phone are on") } },
                { tr("Aanzetten in MediaGuard mislukt: ${it.friendlyMessage()}", "Turning on in MediaGuard failed: ${it.friendlyMessage()}") },
            )
            onDone?.let { cb -> withContext(Dispatchers.Main) { cb(text) } }
        }
    }

    /** Telefoonkanaal nog niet aan na het koppelen (of gekoppeld vóór deze versie): nog eens proberen. */
    private fun retryPushIfPending() {
        if (PushRegistration.shouldRetry(settings.isPaired, state.keyRejected, state.pushPending, state.pushAttemptAt,
                System.currentTimeMillis())) registerPush()
    }

    /** Koppelscherm na een 401: de opgeslagen sleutel nog één keer proberen (bv. na een storing).
     *  Geeft null als het weer werkt, anders de fout in gewone taal. */
    suspend fun retryCurrentKey(): String? {
        val url = settings.serverUrl ?: return tr("Geen adres bewaard. Scan een koppelcode.", "No address saved. Scan a pairing code.")
        val key = settings.apiKey ?: return tr("Geen sleutel bewaard. Scan een koppelcode.", "No key saved. Scan a pairing code.")
        return runCatching { ApiRepository(ApiClient.create(url, key)).me() }.fold(
            {
                state.keyRejected = false
                state.lastContactAt = System.currentTimeMillis()
                Alerts.cancel(this, Alerts.ID_KEY_REJECTED)
                Schedules.apply(this, settings, state, rescheduleDaily = true)
                _session.update { Session(it.generation + 1, paired = true, rejected = false) }
                null
            },
            { it.friendlyMessage() },
        )
    }

    fun unpair() {
        Schedules.cancel(this)
        settings.clear()
        state.clearPairingState()
        state.dailyTargetAt = 0
        Alerts.cancel(this, Alerts.ID_KEY_REJECTED)
        Alerts.cancel(this, Alerts.ID_NO_CONTACT)
        _session.update { Session(it.generation + 1, paired = false, rejected = false) }
    }

    fun consumeNotice() { _notice.value = null }

    /** 401 met de huidige sleutel: niet meer pollen (dat blokkeert anders ook een nieuwe sleutel)
     *  en het koppelscherm tonen. Een 401 van een oudere sleutel telt niet. */
    private val rejection = KeyRejection(
        currentKey = { settings.apiKey },
        alreadyRejected = { state.keyRejected },
        // Alleen als MediaGuard de sleutel ook bij /auth/me weigert is hij echt ingetrokken; een losse
        // 401 van een ander endpoint opent het koppelscherm niet.
        confirm = { key ->
            val url = settings.serverUrl
            url != null && runCatching { ApiRepository(ApiClient.create(url, key)).me() }
                .exceptionOrNull()?.isUnauthorized() == true
        },
    )

    private fun onUnauthorized(key: String) {
        scope.launch { if (rejection.check(key)) markRejected(key) }
    }

    private fun markRejected(key: String) {
        if (key != settings.apiKey || state.keyRejected) return
        state.keyRejected = true
        Schedules.cancel(this)
        Alerts.keyRejected(this)
        _session.update { it.copy(rejected = true) }
    }

    private fun onContact() {
        val now = System.currentTimeMillis()
        if (now - state.lastContactAt > 30_000) state.lastContactAt = now
        if (state.contactAlertFor != 0L) {
            state.contactAlertFor = 0
            Alerts.cancel(this, Alerts.ID_NO_CONTACT)
        }
        retryPushIfPending()
    }

    private fun createChannels() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_DAILY, getString(R.string.channel_daily), NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = getString(R.string.channel_daily_desc) },
        )
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ALERTS, getString(R.string.channel_alerts), NotificationManager.IMPORTANCE_HIGH)
                .apply { description = getString(R.string.channel_alerts_desc) },
        )
    }

    companion object {
        const val CHANNEL_DAILY = "daily"
        const val CHANNEL_ALERTS = "alerts"
    }
}
