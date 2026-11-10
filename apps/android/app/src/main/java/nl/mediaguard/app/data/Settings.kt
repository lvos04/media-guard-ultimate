package nl.mediaguard.app.data

import android.content.Context
import android.content.SharedPreferences

/** Koppeling en voorkeuren. De API-sleutel staat versleuteld (Android Keystore); een beschadigde
 *  opslag laat de app niet meer crashen maar vraagt om opnieuw te koppelen ([recovered]). */
class SettingsStore(private val p: ResilientPrefs) {
    constructor(context: Context) : this(ResilientPrefs({ SecureStore.open(context) }, { SecureStore.wipe(context) }))

    /** De opgeslagen gegevens waren onleesbaar en zijn gewist. */
    val recovered: Boolean get() = p.recovered
    /** Versleutelde opslag werkt helemaal niet: alles staat alleen in het geheugen. */
    val volatileOnly: Boolean get() = p.volatileOnly

    private fun str(key: String): String? = p.read<String?>(null) { it.getString(key, null) }
    private fun put(key: String, v: String?) = p.write { putString(key, v) }

    var serverUrl: String?
        get() = str("url")
        set(v) = put("url", v)

    var apiKey: String?
        get() = str("key")
        set(v) = put("key", v)

    var ntfyServer: String?
        get() = str("ntfy_server")
        set(v) = put("ntfy_server", v)

    var ntfyTopic: String?
        get() = str("ntfy_topic")
        set(v) = put("ntfy_topic", v)

    var ntfyEnabled: Boolean
        get() = p.read(false) { it.getBoolean("ntfy_enabled", false) }
        set(v) = p.write { putBoolean("ntfy_enabled", v) }

    /** Laatste ntfy-bericht-id dat al getoond is (om niets dubbel te melden). */
    var ntfySince: String?
        get() = str("ntfy_since")
        set(v) = put("ntfy_since", v)

    /** Tijdstip van het dagoverzicht in minuten na middernacht (standaard 08:00). */
    var dailyMinutes: Int
        get() = p.read(8 * 60) { it.getInt("daily_minutes", 8 * 60) }
        set(v) = p.write { putInt("daily_minutes", v) }

    var dailyEnabled: Boolean
        get() = p.read(true) { it.getBoolean("daily_enabled", true) }
        set(v) = p.write { putBoolean("daily_enabled", v) }

    val isPaired: Boolean get() = !serverUrl.isNullOrBlank() && !apiKey.isNullOrBlank()

    /** Nieuwe koppeling. Met een ntfy-onderwerp leest de app altijd mee: MediaGuard zet het kanaal
     *  aan zodra de app dat meldt (POST /app/paired), ook als het in de QR-code nog uit stond. */
    fun savePairing(p: PairingPayload) {
        this.p.write {
            putString("url", p.url)
            putString("key", p.key)
            putString("ntfy_server", p.ntfy?.server)
            putString("ntfy_topic", p.ntfy?.topic?.ifBlank { null })
            putBoolean("ntfy_enabled", !p.ntfy?.topic.isNullOrBlank())
            remove("ntfy_since")
        }
    }

    /** Kanaal zoals MediaGuard het na het koppelen of een testbericht teruggeeft. */
    fun saveNtfy(info: NtfyInfo) {
        if (info.topic.isBlank()) return
        val changed = info.topic != ntfyTopic
        p.write {
            putString("ntfy_server", info.server.ifBlank { "https://ntfy.sh" })
            putString("ntfy_topic", info.topic)
            putBoolean("ntfy_enabled", true)
            if (changed) remove("ntfy_since")
        }
    }

    fun clear() = p.write { clear() }
}

/**
 * Niet-geheime toestand van de app (gewone SharedPreferences, vaak geschreven): laatste contact met
 * MediaGuard, laatste ntfy-ophaalactie, het geplande dagoverzicht en of de sleutel geweigerd is.
 */
class StateStore(private val prefs: SharedPreferences) {
    constructor(context: Context) : this(context.getSharedPreferences("mediaguard_state", Context.MODE_PRIVATE))

    private fun long(key: String) = runCatching { prefs.getLong(key, 0L) }.getOrDefault(0L)
    private fun setLong(key: String, v: Long) { runCatching { prefs.edit().putLong(key, v).apply() } }
    private fun bool(key: String) = runCatching { prefs.getBoolean(key, false) }.getOrDefault(false)
    private fun setBool(key: String, v: Boolean) { runCatching { prefs.edit().putBoolean(key, v).apply() } }
    private fun str(key: String) = runCatching { prefs.getString(key, null) }.getOrNull()
    private fun setStr(key: String, v: String?) { runCatching { prefs.edit().putString(key, v).apply() } }

    /** Laatste keer dat MediaGuard antwoordde (epoch ms; 0 = nog nooit). */
    var lastContactAt: Long get() = long("last_contact_at"); set(v) = setLong("last_contact_at", v)
    /** Voor welk [lastContactAt] al een "geen contact"-melding is gestuurd (0 = geen). */
    var contactAlertFor: Long get() = long("contact_alert_for"); set(v) = setLong("contact_alert_for", v)
    var ntfyLastOkAt: Long get() = long("ntfy_last_ok_at"); set(v) = setLong("ntfy_last_ok_at", v)
    var ntfyLastErrorAt: Long get() = long("ntfy_last_error_at"); set(v) = setLong("ntfy_last_error_at", v)
    var ntfyLastError: String? get() = str("ntfy_last_error"); set(v) = setStr("ntfy_last_error", v)
    /** MediaGuard heeft het telefoonkanaal aangezet (POST /app/paired gelukt); 0 = niet (meer) aan. */
    var pushRegisteredAt: Long get() = long("push_registered_at"); set(v) = setLong("push_registered_at", v)
    /** POST /app/paired moet nog lukken: na het koppelen, en bij een app die al gekoppeld was vóór deze
     *  versie (sleutel ontbreekt = true). De app probeert het dan opnieuw bij de start en bij contact. */
    var pushPending: Boolean
        get() = runCatching { prefs.getBoolean("push_pending", true) }.getOrDefault(true)
        set(v) = setBool("push_pending", v)
    /** Laatste poging om het telefoonkanaal aan te zetten (epoch ms), om niet te vaak te proberen. */
    var pushAttemptAt: Long get() = long("push_attempt_at"); set(v) = setLong("push_attempt_at", v)
    /** MediaGuard weigert de opgeslagen sleutel (401): ingetrokken of verlopen. */
    var keyRejected: Boolean get() = bool("key_rejected"); set(v) = setBool("key_rejected", v)
    /** De versleutelde opslag was beschadigd en is gewist; tonen tot er opnieuw gekoppeld is. */
    var storageReset: Boolean get() = bool("storage_reset"); set(v) = setBool("storage_reset", v)
    /** Gepland moment van het volgende dagoverzicht (epoch ms; 0 = niets gepland). */
    var dailyTargetAt: Long get() = long("daily_target_at"); set(v) = setLong("daily_target_at", v)

    fun clearPairingState() {
        runCatching {
            prefs.edit().remove("contact_alert_for").remove("ntfy_last_ok_at").remove("ntfy_last_error_at")
                .remove("ntfy_last_error").remove("push_registered_at").remove("push_pending").remove("push_attempt_at")
                .putBoolean("key_rejected", false)
                .putBoolean("storage_reset", false).apply()
        }
    }
}
