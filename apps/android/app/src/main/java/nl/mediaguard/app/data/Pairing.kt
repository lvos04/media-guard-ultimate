package nl.mediaguard.app.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

@Serializable
data class NtfyInfo(val server: String = "https://ntfy.sh", val topic: String = "", val enabled: Boolean = false)

/** Inhoud van de QR-code uit de web-UI (Instellingen → Android-app koppelen). */
@Serializable
data class PairingPayload(
    val v: Int = 1,
    val url: String = "",
    val key: String = "",
    val ntfy: NtfyInfo? = null,
)

object Pairing {
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    /** Leest de QR-tekst; null bij een ongeldige code (geen JSON, geen URL of sleutel). */
    fun parse(text: String): PairingPayload? {
        val p = runCatching { json.decodeFromString(PairingPayload.serializer(), text.trim()) }.getOrNull()
            ?: return null
        val url = normalizeUrl(p.url) ?: return null
        if (p.key.isBlank()) return null
        return p.copy(url = url, key = p.key.trim())
    }

    /**
     * "mediaguard.example.nl" → "https://mediaguard.example.nl/"; altijd met slash aan het eind.
     * Een publiek adres (tunnel-domein, publiek IP) zonder eigen poort gaat altijd via https:
     * de app-sleutel gaat dan nooit onversleuteld over internet. LAN-adressen en adressen met
     * een eigen poort (bijv. http://nas.lan:30080) blijven zoals ze zijn.
     */
    fun normalizeUrl(raw: String): String? {
        var u = raw.trim()
        if (u.isEmpty()) return null
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "https://$u"
        u = u.trimEnd('/') + "/"
        val parsed = u.toHttpUrlOrNull() ?: return null
        if (parsed.scheme == "http" && (parsed.port == 80 || parsed.port == 443) && isPublicHost(parsed.host)) {
            return parsed.newBuilder().scheme("https").build().toString()
        }
        return parsed.toString()
    }

    private val lanSuffixes = listOf(
        ".local", ".lan", ".home", ".internal", ".localdomain", ".home.arpa", ".intranet",
        ".private", ".localhost", ".fritz.box",
    )

    /** Staat dit adres op internet (en niet op je eigen netwerk)? */
    fun isPublicHost(host: String): Boolean {
        val h = host.lowercase().trimEnd('.')
        if (h.isEmpty()) return false
        if (':' in h) {
            // IPv6: loopback, link-local (fe80::/10) en ULA (fc00::/7) zijn lokaal
            if (h == "::1" || h == "::") return false
            val first = h.substringBefore(':').ifEmpty { "0" }.toIntOrNull(16) ?: return true
            return !((first and 0xfe00) == 0xfc00 || (first and 0xffc0) == 0xfe80)
        }
        val parts = h.split('.')
        if (parts.size == 4 && parts.all { p -> p.toIntOrNull()?.let { it in 0..255 } == true }) {
            val (a, b) = parts.take(2).map { it.toInt() }
            return !(a == 10 || a == 127 || a == 0 || (a == 172 && b in 16..31) ||
                (a == 192 && b == 168) || (a == 169 && b == 254) || (a == 100 && b in 64..127))
        }
        if ('.' !in h || h == "localhost") return false
        return lanSuffixes.none { h.endsWith(it) }
    }
}
