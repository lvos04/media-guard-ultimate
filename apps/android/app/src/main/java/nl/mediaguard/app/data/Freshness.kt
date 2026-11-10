package nl.mediaguard.app.data

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import retrofit2.HttpException
import java.net.InetAddress

/** Hoe oud de getoonde status is: tijdstip van de laatste geslaagde ophaalactie en de laatste fout. */
data class Freshness(val lastOkAt: Long? = null, val error: String? = null)

object Staleness {
    /** Status wordt elke 20 s ververst; ouder dan 90 s (of de laatste poging mislukte) = verouderd. */
    const val MAX_AGE_MS = 90_000L
    /** Na zoveel tijd zonder enig antwoord van MediaGuard volgt een melding op de telefoon. */
    const val NO_CONTACT_ALERT_MS = 3 * 60 * 60 * 1000L

    /** Sinds wanneer de getoonde gegevens verouderd zijn, of null als ze vers zijn (of er nog niets is). */
    fun staleSince(f: Freshness, now: Long, maxAgeMs: Long = MAX_AGE_MS): Long? {
        val ok = f.lastOkAt ?: return null
        return if (f.error != null || now - ok > maxAgeMs) ok else null
    }

    /** Moet er nu een "geen contact"-melding komen? Eén keer per periode zonder contact. */
    fun shouldAlertNoContact(lastContactAt: Long, alertedFor: Long, now: Long, limitMs: Long = NO_CONTACT_ALERT_MS): Boolean =
        lastContactAt > 0 && now - lastContactAt >= limitMs && alertedFor != lastContactAt

    /** Adres dat alleen thuis werkt: http:// (de app stuurt dat alleen naar het eigen netwerk) of een privé-IP. */
    fun lanOnly(serverUrl: String?): Boolean {
        val url = serverUrl?.trim()?.toHttpUrlOrNull() ?: return false
        if (!url.isHttps) return true
        val host = url.host
        val literal = host.contains(':') || host.all { it.isDigit() || it == '.' }
        return literal && runCatching { NetGuard.isLocalAddress(InetAddress.getByName(host)) }.getOrDefault(false)
    }

    /**
     * Geen antwoord is hier verwacht, geen storing: een thuisadres terwijl de telefoon niet op wifi,
     * ethernet of VPN zit (onderweg op mobiele data). Dan geen "MediaGuard reageert niet"-melding.
     */
    fun expectedUnreachable(serverUrl: String?, homeCapableNetwork: Boolean): Boolean =
        !homeCapableNetwork && lanOnly(serverUrl)
}

/** Wanneer de app MediaGuard (opnieuw) vraagt het telefoonkanaal aan te zetten (POST /app/paired). */
object PushRegistration {
    /** Niet vaker dan dit opnieuw proberen (bij de start of bij contact met MediaGuard). */
    const val RETRY_MS = 15 * 60 * 1000L

    fun shouldRetry(paired: Boolean, keyRejected: Boolean, pending: Boolean, lastAttemptAt: Long, now: Long): Boolean =
        paired && !keyRejected && pending && now - lastAttemptAt >= RETRY_MS

    /** Zinloos om het nog eens te proberen: deze sleutel mag het niet (viewer, 403). */
    fun giveUp(e: Throwable): Boolean = e is HttpException && e.code() == 403
}

/** Versies vergelijken zoals "1.2.10" > "1.2.9"; "v1.3.0" en "1.3.0-beta" mogen ook. */
object Versions {
    fun parse(v: String?): List<Int>? {
        val core = v?.trim()?.removePrefix("v")?.removePrefix("V")?.substringBefore('-')?.substringBefore('+')
            ?.takeIf { it.isNotBlank() } ?: return null
        val parts = core.split('.').map { it.toIntOrNull() ?: return null }
        return parts.takeIf { it.isNotEmpty() }
    }

    fun compare(a: String?, b: String?): Int? {
        val x = parse(a) ?: return null
        val y = parse(b) ?: return null
        for (i in 0 until maxOf(x.size, y.size)) {
            val d = x.getOrElse(i) { 0 } - y.getOrElse(i) { 0 }
            if (d != 0) return d
        }
        return 0
    }

    fun newer(a: String?, than: String?): Boolean = (compare(a, than) ?: 0) > 0
}

/**
 * Wat de app over updates weet. De app en MediaGuard hebben hetzelfde versienummer (één release);
 * draait de server een nieuwere versie dan de app, dan is er dus een nieuwere app. De nieuwste
 * release komt uit de updatecontrole van de server (GET /system/update), als die bestaat.
 */
data class UpdateInfo(
    val serverVersion: String?,
    val appVersion: String,
    val latest: String? = null,
    val appLatest: String? = null,
    val url: String? = null,
    /** Directe link naar de nieuwste APK (``android.url``), als de server die kent. */
    val appUrl: String? = null,
) {
    /** Alleen "server draait nieuwer dan de app"; zonder de nieuwste release uit de updatecontrole
     *  (die staat dan al als punt in "Wacht op jou"). */
    fun withoutLatest(): UpdateInfo = copy(latest = null, appLatest = null)

    /** Link voor de knop: de APK bij een nieuwere app, anders de release. */
    val link: String? get() = (if (appUpdate != null && serverUpdate == null) appUrl ?: url else url ?: appUrl)

    /** Nieuwere MediaGuard-versie dan de server draait. */
    val serverUpdate: String? get() = latest?.takeIf { serverVersion != null && Versions.newer(it, serverVersion) }

    /** Nieuwste app-versie die nieuwer is dan deze app (van de server of de release). */
    val appUpdate: String?
        get() = listOfNotNull(appLatest ?: latest, serverVersion)
            .filter { Versions.newer(it, appVersion) }
            .maxWithOrNull { a, b -> Versions.compare(a, b) ?: 0 }

    /** Korte zin voor de banner op Overzicht; null als alles bij is. */
    fun headline(): String? = when {
        serverUpdate != null && appUpdate != null -> "MediaGuard $serverUpdate is uit (server en app)"
        serverUpdate != null -> "MediaGuard $serverUpdate is uit; je server draait $serverVersion"
        appUpdate != null -> "Nieuwere app beschikbaar: $appUpdate (jij hebt $appVersion)"
        else -> null
    }

    companion object {
        private val LATEST = listOf("latest", "latest_version", "available_version", "newest", "version_latest", "new_version")
        private val APP = listOf("android", "app", "apk", "android_app")
        private val URLS = listOf("url", "html_url", "release_url", "notes_url", "download_url")

        /** Leest het antwoord van GET /system/update zonder een vaste vorm aan te nemen. Heeft de gebruiker
         *  de nieuwste versie verborgen (`dismissed`), dan telt alleen nog server- tegenover app-versie. */
        fun from(json: JsonElement?, serverVersion: String?, appVersion: String): UpdateInfo {
            val o = json as? JsonObject ?: return UpdateInfo(serverVersion, appVersion)
            val dismissed = (o["dismissed"] as? JsonPrimitive)?.booleanOrNull == true
            val current0 = (o["current"] as? JsonPrimitive)?.contentOrNull
            if (dismissed) return UpdateInfo(serverVersion ?: current0, appVersion)
            val nested = listOfNotNull(o, o["server"] as? JsonObject, o["release"] as? JsonObject, o["update"] as? JsonObject)
            fun text(obj: JsonObject, keys: List<String>): String? = keys.firstNotNullOfOrNull { k ->
                when (val v = obj[k]) {
                    is JsonPrimitive -> v.contentOrNull?.takeIf { it.isNotBlank() }
                    is JsonObject -> (v["version"] as? JsonPrimitive)?.contentOrNull ?: (v["tag"] as? JsonPrimitive)?.contentOrNull
                    else -> null
                }
            }
            fun clean(v: String?) = v?.trim()?.removePrefix("v")?.removePrefix("V")?.takeIf { Versions.parse(it) != null }
            val latest = clean(nested.firstNotNullOfOrNull { text(it, LATEST) ?: text(it, listOf("tag", "tag_name")) })
            val app = APP.firstNotNullOfOrNull { k ->
                when (val v = o[k]) {
                    is JsonObject -> text(v, listOf("latest", "version", "latest_version"))
                    is JsonPrimitive -> v.contentOrNull
                    else -> null
                }
            }.let(::clean)
            val url = nested.firstNotNullOfOrNull { text(it, URLS) }?.takeIf { it.startsWith("https://") }
            val appUrl = APP.firstNotNullOfOrNull { k -> (o[k] as? JsonObject)?.let { text(it, URLS) } }
                ?.takeIf { it.startsWith("https://") }
            val current = nested.firstNotNullOfOrNull { text(it, listOf("current", "current_version", "installed")) }
            return UpdateInfo(serverVersion ?: current, appVersion, latest, app, url, appUrl)
        }
    }
}

/**
 * Beslist of een 401 echt betekent dat de sleutel ingetrokken is: alleen voor de huidige sleutel
 * (een 401 van een oude sleutel na opnieuw koppelen telt niet), één controle tegelijk, en alleen als
 * [confirm] (GET /auth/me met dezelfde sleutel) het bevestigt.
 */
class KeyRejection(
    private val currentKey: () -> String?,
    private val alreadyRejected: () -> Boolean,
    private val confirm: suspend (String) -> Boolean,
) {
    private val checking = java.util.concurrent.atomic.AtomicBoolean(false)

    suspend fun check(key: String): Boolean {
        if (key != currentKey() || alreadyRejected() || !checking.compareAndSet(false, true)) return false
        return try { confirm(key) && key == currentKey() } finally { checking.set(false) }
    }
}
